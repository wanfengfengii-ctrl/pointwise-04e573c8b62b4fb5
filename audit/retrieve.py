from dataclasses import dataclass

from audit.issue import issue_first_receipt
from audit.receipts import Receipt
from batch.freeze import FrozenBatch, load_frozen
from media.readback import read_copy


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


def read_from_volume(volume, batch_id, *, expected_copy=None):
    checked = read_copy(volume, batch_id, expected_copy=expected_copy)
    return ArchiveReport(checked.batch, checked.receipt)


def read(batch_id, *, source=None, catalogue=None, volume=None, expected_copy=None):
    if volume is not None:
        if source is not None or catalogue is not None:
            raise ValueError('select one read boundary')
        return read_from_volume(volume, batch_id, expected_copy=expected_copy)
    if source is None or catalogue is None or expected_copy is not None:
        raise ValueError('source read requires the source and catalogue')
    batch = load_frozen(source, batch_id)
    return ArchiveReport(batch, issue_first_receipt(catalogue, batch))
