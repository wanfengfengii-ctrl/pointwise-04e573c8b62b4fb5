from dataclasses import dataclass

from audit.fixity import FrozenSeal, seal_frozen_rows, verify_frozen_seal
from audit.quantity import sum_millilitres
from media.volume import CopyIdentity, copy_fields


@dataclass(frozen=True)
class CopyManifest:
    identity: CopyIdentity
    seal: FrozenSeal


def build_copy_manifest(identity, batch):
    copy_fields(identity)
    if identity.batch_id != batch.batch_id:
        raise ValueError('copy and frozen batch identities differ')
    return CopyManifest(identity, seal_frozen_rows(batch))


def validate_copy(manifest, batch, receipt, *, expected_copy=None):
    if expected_copy is not None and manifest.identity != expected_copy:
        raise ValueError('wrong volume copy')
    if manifest.identity.batch_id != batch.batch_id or receipt.batch_id != batch.batch_id:
        raise ValueError('copy, snapshot and receipt must bind the same batch')
    verify_frozen_seal(batch, manifest.seal)
    if (receipt.seal != manifest.seal
            or receipt.receipt_id != 'receipt-' + manifest.seal.digest
            or receipt.total_millilitres != sum_millilitres(batch.rows)):
        raise ValueError('copy disagrees with its first receipt')
