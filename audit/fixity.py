from dataclasses import dataclass
import hashlib
import json

from batch.freeze import frozen_batch_id
from batch.lineage import encode_rows


@dataclass(frozen=True)
class FrozenSeal:
    row_count: int
    digest: str


def seal_frozen_rows(batch):
    if frozen_batch_id(batch.source_id, batch.day_id, batch.revision) != batch.batch_id:
        raise ValueError('frozen identity does not match its source revision')
    rows = encode_rows(batch.rows)
    record = [1, batch.batch_id, batch.source_id, batch.day_id, batch.revision, rows]
    content = json.dumps(record, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return FrozenSeal(len(rows), hashlib.sha256(content).hexdigest())


def verify_frozen_seal(batch, seal):
    if seal_frozen_rows(batch) != seal:
        raise ValueError('frozen seal does not match ordered observations')
