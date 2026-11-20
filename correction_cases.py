from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from audit.receipts import load_receipt
from audit.retrieve import ArchiveReport, read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.storage import initialize_source_store, open_source_store
from custody.catalogue import initialize_catalogue, open_catalogue
from custody.corrections import publish_correction
from custody.registry import register_volume_domain
from custody.release import release
from custody.verification import verify
from media.readback import PendingCorrectionError
from media.replicate import replicate
from media.volume import CopyIdentity, VolumeIdentity, initialize_volume, open_volume


DAY_ID = "2026-10-04"
ROWS = tuple(ObservationRow("estuary", number, value, SourceReference("station-ledger", number + 20))
             for number, value in enumerate((7000, None, 11000), 1))
CORRECTION = ("estuary", 1, 8000, SourceReference("late-note", 5))


@dataclass(frozen=True)
class SampleDay:
    old: ArchiveReport
    copies: dict[str, CopyIdentity]
    ordinary_control: ArchiveReport | None


def seed_day(root, *, add_same_domain=False, ordinary_control=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source_path, catalogue_path = root / "source.sqlite", root / "catalogue.sqlite"
    if catalogue_path.exists() and not ordinary_control:
        return _load_seeded_day(root, add_same_domain=add_same_domain)
    if catalogue_path.exists():
        raise ValueError("the V1 ordinary control can only run in a fresh work directory")
    initialize_source_store(source_path, "basin-stations")
    initialize_catalogue(catalogue_path, "basin-archive")
    with closing(open_catalogue(catalogue_path)) as catalogue:
        with closing(open_source_store(source_path)) as source:
            revision = intake(source, DAY_ID, ROWS)
            batch = freeze(source, DAY_ID, revision)
            old = read(batch.batch_id, source=source, catalogue=catalogue)
            ordinary = None
            if ordinary_control:
                later = intake(source, DAY_ID, previous_revision=revision, correction=CORRECTION)
                control = freeze(source, DAY_ID, later)
                ordinary = read(control.batch_id, source=source, catalogue=catalogue)
        stations = [("C", "C"), ("D", "D")]
        if add_same_domain:
            stations.append(("C2", "C"))
        copies = {}
        for station, domain in stations:
            path = root / f"{station}.sqlite"
            identity = VolumeIdentity(f"station-{station}", "archive", domain)
            key = sha256(f"correction-sample-{station}".encode()).digest()
            initialize_volume(path, identity, "basin-archive", key)
            with closing(open_volume(path)) as volume:
                register_volume_domain(catalogue, volume)
                copies[station] = verify(catalogue, volume, replicate(old, volume, f"old-{station}"))
        if not release(catalogue, old.batch_id):
            raise AssertionError("old batch was not released")
    return SampleDay(old, copies, ordinary)


def _load_seeded_day(root, *, add_same_domain=False):
    """Reopen a previously seeded day without re-intaking its observations."""
    stations = ["C", "D"] + (["C2"] if add_same_domain else [])
    with closing(open_catalogue(root / "catalogue.sqlite")) as catalogue, \
            closing(open_volume(root / "C.sqlite")) as first:
        from media.volume import locate_batch_copy
        batch_id = first.execute(
            "SELECT batch_id FROM copies ORDER BY rowid LIMIT 1").fetchone()[0]
        old = read(batch_id, volume=first)
        copies = {"C": locate_batch_copy(first, batch_id)}
        for station in stations[1:]:
            path = root / f"{station}.sqlite"
            if not path.exists():
                raise ValueError(f"seeded work directory has no {station} volume")
            with closing(open_volume(path)) as volume:
                copies[station] = locate_batch_copy(volume, batch_id)
    return SampleDay(old, copies, None)


class StopAfter:
    def __init__(self, event, site=None):
        self.event = event
        self.site = site
        self.batch_id = None
        self.target = None

    def __call__(self, event, batch_id, target):
        if event == self.event and (self.site is None or (target and target.volume.site == self.site)):
            self.batch_id = batch_id
            self.target = target
            raise InterruptedError(f"stopped after {event}")


def run_v1_control(root):
    day = seed_day(root, ordinary_control=True)
    ordinary = day.ordinary_control
    with closing(open_catalogue(root / "catalogue.sqlite")) as catalogue:
        with closing(open_volume(root / "C.sqlite")) as c:
            copy = replicate(ordinary, c, "ordinary-C")
            verify(catalogue, c, copy)
        if release(catalogue, ordinary.batch_id):
            raise AssertionError("ordinary child should have just one accepted domain")
    totals = {}
    with closing(open_volume(root / "C.sqlite")) as c, closing(open_volume(root / "D.sqlite")) as d:
        for name, volume in (("C", c), ("D", d)):
            old = read(day.old.batch_id, volume=volume, expected_copy=day.copies[name])
            if old != day.old or old.rows != ROWS or old.total_millilitres != 18000:
                raise AssertionError(f"old report mismatch on {name}")
            totals[name] = old.total_millilitres
        control = read(ordinary.batch_id, volume=c, expected_copy=copy)
        if control != ordinary or control.total_millilitres != 19000:
            raise AssertionError("ordinary one-domain control mismatch")
        try:
            read(ordinary.batch_id, volume=d)
        except KeyError:
            pass
        else:
            raise AssertionError("D unexpectedly has the ordinary control")
    print(json.dumps({"v1_old_mL": totals, "v1_ordinary_C_mL": control.total_millilitres,
                      "v1_ordinary_released": False}, sort_keys=True))


def run_correction_handover(root):
    """Drive the late-correction handover, including an interrupted first prepare."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    resume = (root / "catalogue.sqlite").exists()
    day = seed_day(root)
    catalogue_path = root / "catalogue.sqlite"
    with closing(open_catalogue(catalogue_path)) as catalogue, \
            closing(open_volume(root / "C.sqlite")) as c, \
            closing(open_volume(root / "D.sqlite")) as d:
        targets = ((c, "new-C"), (d, "new-D"))
        if not resume:
            stopper = StopAfter("prepared", "station-C")
            try:
                publish_correction(catalogue, "late-1", c, day.copies["C"], CORRECTION,
                                   targets, after_event=stopper)
            except InterruptedError:
                pass
            else:
                raise AssertionError("the prepared-stage interruption did not fire")
            pending_id = stopper.batch_id
            try:
                read(pending_id, volume=c)
            except PendingCorrectionError:
                pending = "C reports PendingCorrectionError while awaiting publication"
            else:
                raise AssertionError("C must not serve the candidate before publication")
            try:
                read(pending_id, volume=d)
            except KeyError:
                missing = "D reports the successor number as unknown"
            else:
                raise AssertionError("D never prepared the successor number")
        else:
            pending_id = catalogue.execute(
                "SELECT batch_id FROM correction_requests WHERE request_id=?",
                ("late-1",)).fetchone()[0]
            pending = "resumed from the persisted prepared stage"
            missing = "resumed from the persisted request binding"
        # The old ledgers remain fully serviceable throughout the interruption.
        old_totals = {
            name: read(day.old.batch_id, volume=volume,
                       expected_copy=day.copies[name]).total_millilitres
            for name, volume in (("C", c), ("D", d))
        }
        # Reopen with the same request id and the same bindings.
        batch_id, receipt = publish_correction(
            catalogue, "late-1", c, day.copies["C"], CORRECTION, targets)
        if batch_id != pending_id:
            raise AssertionError("reopen fixed a different successor number")
        if load_receipt(catalogue, batch_id) != receipt:
            raise AssertionError("catalogue first receipt differs from the handover receipt")
        new_ledgers = {}
        for name, volume, copy_id in (("C", c, "new-C"), ("D", d, "new-D")):
            target = CopyIdentity(_identity(volume), batch_id, copy_id)
            report = read(batch_id, volume=volume, expected_copy=target)
            values = [row.millilitres for row in report.rows]
            if values != [8000, None, 11000] or report.total_millilitres != 19000:
                raise AssertionError(f"new ledger mismatch on {name}")
            if report.receipt != receipt or report.request_id != "late-1":
                raise AssertionError(f"receipt lineage mismatch on {name}")
            if report.parent_receipt != day.old.receipt or report.before != ROWS[0]:
                raise AssertionError(f"parent lineage mismatch on {name}")
            if report.correction_source != CORRECTION[3]:
                raise AssertionError(f"correction source mismatch on {name}")
            new_ledgers[name] = {
                "values": values,
                "total_millilitres": report.total_millilitres,
                "receipt_id": report.receipt.receipt_id,
                "sources": [[row.source.document, row.source.line] for row in report.rows],
            }
    print(json.dumps({
        "case_id": "late-correction-handover",
        "interruption": {"prepared": pending, "unprepared": missing,
                         "old_ledger_mL": old_totals},
        "successor_batch_id": batch_id,
        "new_ledger": new_ledgers,
        "lineage": {
            "parent_batch_id": day.old.batch_id,
            "parent_receipt_id": day.old.receipt.receipt_id,
            "before_millilitres": ROWS[0].millilitres,
            "correction_source": [CORRECTION[3].document, CORRECTION[3].line],
        },
    }, sort_keys=True))


def _identity(volume):
    from media.volume import volume_settings
    return volume_settings(volume).identity


if __name__ == "__main__":
    with TemporaryDirectory() as control_dir, TemporaryDirectory() as handover_dir:
        run_v1_control(Path(control_dir))
        run_correction_handover(Path(handover_dir))
