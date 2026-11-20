from archive_store import require_name, transaction
from audit.receipts import receipt_fields
from batch.lineage import encode_rows
from media.integrity import build_copy_manifest, validate_copy
from media.readback import _read_copy_snapshot
from media.volume import CopyIdentity, volume_settings


def replicate(report, volume, copy_id):
    require_name(copy_id, 'copy identity')
    batch, receipt = report.batch, report.receipt
    target = CopyIdentity(volume_settings(volume).identity, batch.batch_id, copy_id)
    manifest = build_copy_manifest(target, batch)
    validate_copy(manifest, batch, receipt)
    rows = encode_rows(batch.rows)
    with transaction(volume, write=True):
        if volume.execute('SELECT 1 FROM copy_exits WHERE copy_id=?', (copy_id,)).fetchone():
            raise ValueError('retired copy identity cannot be reused')
        existing = volume.execute(
            'SELECT copy_id, batch_id FROM copies WHERE copy_id=? OR batch_id=?',
            (copy_id, batch.batch_id),
        ).fetchall()
        if existing:
            if existing != [(copy_id, batch.batch_id)]:
                raise ValueError('volume already holds a different copy or batch for that identity')
            original = _read_copy_snapshot(volume, batch.batch_id, target)
            if (original.batch, original.receipt) != (batch, receipt):
                raise ValueError('replication differs from the committed copy')
            return target
        volume.execute('INSERT INTO copies VALUES (?, ?, ?, ?, ?, ?, ?)', (
            copy_id, batch.batch_id, batch.source_id, batch.day_id, batch.revision,
            manifest.seal.row_count, manifest.seal.digest,
        ))
        volume.executemany('INSERT INTO copy_rows VALUES (?, ?, ?, ?, ?, ?, ?)',
                           ((copy_id, position, *row) for position, row in enumerate(rows)))
        volume.execute('INSERT INTO copy_receipts VALUES (?, ?, ?, ?, ?)',
                       (copy_id, *receipt_fields(receipt)))
    return target
