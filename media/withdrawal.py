from dataclasses import dataclass
import hmac
import json

from archive_store import transaction
from media.volume import CopyIdentity, copy_fields, volume_settings


@dataclass(frozen=True)
class ExitResult:
    catalogue_id: str
    request_id: str
    copy: CopyIdentity
    permit_signature: str
    signature: str


def _exit_signature(key, catalogue_id, request_id, copy, permit_signature):
    payload = ['archive-exit-v1', catalogue_id, request_id, *copy_fields(copy), permit_signature]
    return hmac.digest(key, json.dumps(payload, separators=(',', ':')).encode('ascii'), 'sha256').hex()


def validate_exit_result(key, result):
    expected = _exit_signature(key, result.catalogue_id, result.request_id,
                               result.copy, result.permit_signature)
    if not isinstance(result.signature, str) or not result.signature.isascii():
        raise ValueError('invalid exit result signature')
    if not hmac.compare_digest(expected, result.signature):
        raise ValueError('exit result authentication failed')


def load_exit_result(volume, request_id):
    stored = volume.execute('''
        SELECT copy_id, batch_id, permit_signature, signature FROM copy_exits WHERE request_id=?
    ''', (request_id,)).fetchone()
    if stored is None:
        return None
    copy_id, batch_id, permit_signature, signature = stored
    settings = volume_settings(volume)
    return ExitResult(settings.catalogue_id, request_id, CopyIdentity(settings.identity, batch_id, copy_id),
                      permit_signature, signature)


def exit_copy(volume, permit):
    from retire.permits import validate_permit

    with transaction(volume, write=True):
        settings = volume_settings(volume)
        validate_permit(permit, settings)
        request = permit.request
        old = load_exit_result(volume, request.request_id)
        if old is not None:
            if old.copy != request.copy or old.permit_signature != permit.signature:
                raise ValueError('exit replay conflicts with the first result')
            validate_exit_result(settings.permit_key, old)
            return old
        cursor = volume.execute('DELETE FROM copies WHERE batch_id=? AND copy_id=?',
                                (request.batch_id, request.copy.copy_id))
        if cursor.rowcount != 1:
            raise KeyError(request.copy)
        signature = _exit_signature(settings.permit_key, settings.catalogue_id, request.request_id,
                                    request.copy, permit.signature)
        volume.execute('INSERT INTO copy_exits VALUES (?, ?, ?, ?, ?)', (
            request.request_id, request.copy.copy_id, request.batch_id, permit.signature, signature,
        ))
        return ExitResult(settings.catalogue_id, request.request_id, request.copy, permit.signature, signature)
