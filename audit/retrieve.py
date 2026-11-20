from dataclasses import dataclass

from audit.issue import issue_first_receipt
from audit.receipts import Receipt
from batch.freeze import FrozenBatch, load_frozen
from media.corrections import correction_status
from media.readback import read_copy, read_volume_material


@dataclass(frozen=True)
class ArchiveReport:
    batch: FrozenBatch
    receipt: Receipt

    @property
    def batch_id(self):
        return self.batch.batch_id

    @property
    def rows(self):
        return self.batch.rows

    @property
    def total_millilitres(self):
        return self.receipt.total_millilitres


@dataclass(frozen=True)
class CorrectionReport:
    """Readback of a published correction successor from one target volume."""

    batch_id: str
    receipt: Receipt
    rows: tuple
    total_millilitres: int
    request_id: str
    parent_copy: object
    parent_receipt: Receipt
    before: object
    correction_source: object


def read_from_volume(volume, batch_id, *, expected_copy=None):
    if correction_status(volume, batch_id) is None:
        checked = read_copy(volume, batch_id, expected_copy=expected_copy)
        return ArchiveReport(checked.batch, checked.receipt)
    # A correction candidate: read_volume_material raises PendingCorrectionError
    # when the formal publication basis has not reached this volume yet.
    candidate, rows = read_volume_material(volume, batch_id, expected_copy=expected_copy)
    return CorrectionReport(
        candidate.batch_id, candidate.receipt, rows, candidate.total_millilitres,
        candidate.request_id, candidate.parent_copy, candidate.parent_receipt,
        candidate.before, candidate.correction_source,
    )


def read(batch_id, *, source=None, catalogue=None, volume=None, expected_copy=None):
    if volume is not None:
        if source is not None or catalogue is not None:
            raise ValueError('select one read boundary')
        return read_from_volume(volume, batch_id, expected_copy=expected_copy)
    if source is None or catalogue is None or expected_copy is not None:
        raise ValueError('source read requires the source and catalogue')
    batch = load_frozen(source, batch_id)
    return ArchiveReport(batch, issue_first_receipt(catalogue, batch))
