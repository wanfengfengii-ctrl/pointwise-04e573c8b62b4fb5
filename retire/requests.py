from dataclasses import dataclass

from archive_store import require_name
from media.volume import CopyIdentity, VolumeIdentity, copy_fields


@dataclass(frozen=True)
class RetireRequest:
    request_id: str
    batch_id: str
    copy: CopyIdentity


@dataclass(frozen=True)
class RequestRecord:
    request: RetireRequest
    status: str
    permit_signature: str | None
    terminal_signature: str | None
    reason: str | None


def request_fields(request):
    if not isinstance(request, RetireRequest):
        raise ValueError('expected a retirement request')
    request_id = require_name(request.request_id, 'retirement request')
    site, volume, domain, batch_id, copy_id = copy_fields(request.copy)
    if request.batch_id != batch_id:
        raise ValueError('request and copy batch identities differ')
    return request_id, batch_id, site, volume, domain, copy_id


def initialize_requests(database):
    database.execute('''CREATE TABLE IF NOT EXISTS retire_requests (
        request_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES receipts(batch_id),
        site TEXT NOT NULL, volume TEXT NOT NULL, domain TEXT NOT NULL, copy_id TEXT NOT NULL,
        status TEXT NOT NULL, permit_signature TEXT, terminal_signature TEXT, reason TEXT,
        CHECK((status='rejected' AND reason IS NOT NULL AND permit_signature IS NULL AND terminal_signature IS NULL)
           OR (status='permitted' AND reason IS NULL AND permit_signature IS NOT NULL AND terminal_signature IS NULL)
           OR (status='settled' AND reason IS NULL AND permit_signature IS NOT NULL AND terminal_signature IS NOT NULL))
    )''')


def load_request(database, request_id):
    require_name(request_id, 'retirement request')
    record = database.execute('''
        SELECT batch_id, site, volume, domain, copy_id, status, permit_signature, terminal_signature, reason
        FROM retire_requests WHERE request_id=?
    ''', (request_id,)).fetchone()
    if record is None:
        return None
    batch_id, site, volume, domain, copy_id, status, permit, terminal, reason = record
    copy = CopyIdentity(VolumeIdentity(site, volume, domain), batch_id, copy_id)
    return RequestRecord(RetireRequest(request_id, batch_id, copy), status, permit, terminal, reason)


def record_rejection(database, request, reason):
    database.execute('''INSERT INTO retire_requests VALUES (?, ?, ?, ?, ?, ?, 'rejected', NULL, NULL, ?)''',
                     (*request_fields(request), require_name(reason, 'rejection reason')))


def record_permit(database, request, signature):
    database.execute('''INSERT INTO retire_requests VALUES (?, ?, ?, ?, ?, ?, 'permitted', ?, NULL, NULL)''',
                     (*request_fields(request), signature))


def record_terminal(database, request_id, signature):
    result = database.execute('''
        UPDATE retire_requests SET status='settled', terminal_signature=?
        WHERE request_id=? AND status='permitted'
    ''', (signature, request_id))
    if result.rowcount != 1:
        raise ValueError('only a pending permit can be settled')
