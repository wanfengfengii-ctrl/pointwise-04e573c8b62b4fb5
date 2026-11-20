from dataclasses import dataclass

from archive_store import transaction
from audit.fixity import FrozenSeal
from audit.receipts import Receipt, receipt_from_fields
from batch.freeze import FrozenBatch
from batch.lineage import decode_ordered_rows
from media.corrections import correction_status, read_candidate
from media.integrity import CopyManifest, validate_copy
from media.volume import locate_batch_copy


class PendingCorrectionError(ValueError):
    """The volume holds a correction candidate that has no publication basis yet."""


@dataclass(frozen=True)
class VolumeReadback:
    manifest: CopyManifest
    batch: FrozenBatch
    receipt: Receipt


def _read_copy_snapshot(volume, batch_id, expected_copy):
    identity = locate_batch_copy(volume, batch_id)
    if expected_copy is not None and identity != expected_copy:
        raise ValueError('wrong volume copy')
    source_id, day_id, revision, count, digest = volume.execute('''
        SELECT source_id, day_id, revision, row_count, digest FROM copies WHERE copy_id=?
    ''', (identity.copy_id,)).fetchone()
    records = volume.execute('''
        SELECT position, station, row_number, reading, document, source_line
        FROM copy_rows WHERE copy_id=? ORDER BY position
    ''', (identity.copy_id,))
    rows = decode_ordered_rows(records)
    fields = volume.execute('''
        SELECT receipt_id, row_count, digest, total FROM copy_receipts WHERE copy_id=?
    ''', (identity.copy_id,)).fetchone()
    if fields is None:
        raise ValueError('volume copy has no first receipt')
    receipt = receipt_from_fields(batch_id, fields)
    batch = FrozenBatch(batch_id, source_id, day_id, revision, rows)
    manifest = CopyManifest(identity, FrozenSeal(count, digest))
    validate_copy(manifest, batch, receipt, expected_copy=expected_copy)
    return VolumeReadback(manifest, batch, receipt)


def read_copy(volume, batch_id, *, expected_copy=None):
    with transaction(volume):
        return _read_copy_snapshot(volume, batch_id, expected_copy)


def read_volume_material(volume, batch_id, *, expected_copy=None):
    """Read either a V1 copy or a published correction from a volume.

    Correction candidates that have not received their formal publication
    basis raise :class:`PendingCorrectionError`; a batch the volume never
    prepared raises :class:`KeyError`.
    """
    with transaction(volume):
        try:
            return _read_copy_snapshot(volume, batch_id, expected_copy)
        except KeyError:
            status = correction_status(volume, batch_id)
            if status is None:
                raise
        copy_id, published = status
        from media.volume import CopyIdentity, volume_settings
        target = CopyIdentity(volume_settings(volume).identity, batch_id, copy_id)
        if expected_copy is not None and target != expected_copy:
            raise ValueError('wrong volume copy')
        candidate, rows = read_candidate(volume, batch_id, expected_copy=target)
        if not published:
            raise PendingCorrectionError(batch_id)
        return candidate, rows
