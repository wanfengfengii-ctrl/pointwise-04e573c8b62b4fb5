from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from audit.retrieve import ArchiveReport, read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.storage import initialize_source_store, open_source_store
from custody.catalogue import initialize_catalogue, open_catalogue
from custody.registry import register_volume_domain
from custody.release import release
from custody.verification import verify
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
    source_path, catalogue_path = root / "source.sqlite", root / "catalogue.sqlite"
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


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        run_v1_control(Path(directory))
