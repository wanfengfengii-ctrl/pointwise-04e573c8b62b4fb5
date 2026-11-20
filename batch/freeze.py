from dataclasses import dataclass
import hashlib
import json

from archive_store import require_name, store_identity, transaction
from batch.lineage import ObservationRow
from batch.revisions import check_revision_number, load_revision
from batch.storage import fetch_frozen_snapshot, store_frozen_snapshot


@dataclass(frozen=True)
class FrozenBatch:
    batch_id: str
    source_id: str
    day_id: str
    revision: int
    rows: tuple[ObservationRow, ...]


def frozen_batch_id(source_id, day_id, revision):
    require_name(source_id, 'source identity')
    require_name(day_id, 'day identity')
    check_revision_number(revision)
    key = json.dumps([source_id, day_id, revision], separators=(',', ':')).encode('ascii')
    return 'batch-' + hashlib.sha256(key).hexdigest()


def freeze(database, day_id, revision):
    source_id = store_identity(database, 'source')
    batch_id = frozen_batch_id(source_id, day_id, revision)
    with transaction(database, write=True):
        known = database.execute('SELECT 1 FROM frozen_batches WHERE batch_id=?', (batch_id,)).fetchone()
        if known is None:
            record = load_revision(database, day_id, revision)
            store_frozen_snapshot(database, batch_id, day_id, revision, record.rows)
    return load_frozen(database, batch_id)


def load_frozen(database, batch_id):
    require_name(batch_id, 'frozen batch identity')
    with transaction(database):
        source_id = store_identity(database, 'source')
        day_id, revision, rows = fetch_frozen_snapshot(database, batch_id)
        if frozen_batch_id(source_id, day_id, revision) != batch_id:
            raise ValueError('frozen batch identity does not match its snapshot')
    return FrozenBatch(batch_id, source_id, day_id, revision, rows)
