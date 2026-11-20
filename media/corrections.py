"""Station-side storage for late corrections of already released batches.

A volume first persists a *candidate* (full corrected text plus lineage) while
the catalogue has not published a successor receipt yet.  Candidates are not
independently retrievable until the catalogue's formal publication basis is
delivered to the volume; see :class:`media.readback.PendingCorrectionError`.
"""
from dataclasses import dataclass

from archive_store import require_name, transaction
from audit.fixity import FrozenSeal
from audit.receipts import Receipt
from batch.lineage import (
    ObservationRow, SourceReference, decode_integer, decode_ordered_rows,
    encode_rows, integer_text, validate_source_reference,
)
from media.volume import CopyIdentity, VolumeIdentity, copy_fields, volume_settings


_CORRECTION_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS correction_candidates (
        copy_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        parent_site TEXT NOT NULL, parent_volume TEXT NOT NULL, parent_domain TEXT NOT NULL,
        parent_batch_id TEXT NOT NULL, parent_copy_id TEXT NOT NULL,
        parent_receipt_id TEXT NOT NULL, parent_row_count INTEGER NOT NULL,
        parent_digest TEXT NOT NULL, parent_total TEXT NOT NULL,
        before_station TEXT NOT NULL, before_row TEXT NOT NULL, before_reading TEXT,
        before_document TEXT NOT NULL, before_line TEXT NOT NULL,
        correction_document TEXT NOT NULL, correction_line TEXT NOT NULL,
        row_count INTEGER NOT NULL, digest TEXT NOT NULL, total TEXT NOT NULL,
        published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0, 1))
    )''',
    '''CREATE TABLE IF NOT EXISTS correction_candidate_rows (
        copy_id TEXT NOT NULL REFERENCES correction_candidates(copy_id),
        position INTEGER NOT NULL, station TEXT NOT NULL, row_number TEXT NOT NULL,
        reading TEXT, document TEXT NOT NULL, source_line TEXT NOT NULL,
        PRIMARY KEY(copy_id, position)
    )''',
)


def initialize_corrections(database):
    for statement in _CORRECTION_SCHEMA:
        database.execute(statement)


@dataclass(frozen=True)
class CorrectionCandidate:
    """A candidate materialized on one volume, bound to its full lineage."""

    target: CopyIdentity
    request_id: str
    parent_copy: CopyIdentity
    parent_receipt: Receipt
    before: ObservationRow
    correction_source: SourceReference
    row_count: int
    digest: str
    total_millilitres: int
    published: bool

    @property
    def batch_id(self):
        return self.target.batch_id

    @property
    def receipt(self):
        return Receipt('receipt-correction-' + self.digest, self.batch_id,
                       FrozenSeal(self.row_count, self.digest), self.total_millilitres)


def prepare_candidate(volume, candidate, rows):
    """Persist a complete correction candidate on the target volume."""
    copy_fields(candidate.target)
    validate_source_reference(candidate.correction_source)
    validate_source_reference(candidate.before.source)
    encoded = encode_rows(rows)
    if len(encoded) != candidate.row_count:
        raise ValueError('candidate row count does not match its material')
    settings = volume_settings(volume)
    if settings.identity != candidate.target.volume:
        raise ValueError('correction target does not belong to this volume')
    if volume.execute('SELECT 1 FROM copies WHERE batch_id=?',
                      (candidate.batch_id,)).fetchone():
        raise ValueError('correction successor id collides with a V1 batch on this volume')
    parent = candidate.parent_copy
    copy_fields(parent)
    require_name(candidate.request_id, 'correction request')
    with transaction(volume, write=True):
        existing = volume.execute(
            'SELECT batch_id FROM correction_candidates WHERE copy_id=?',
            (candidate.target.copy_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != candidate.batch_id:
                raise ValueError('candidate copy identity already binds a different successor')
            return candidate
        if volume.execute(
            'SELECT 1 FROM correction_candidates WHERE batch_id=? OR request_id=?',
            (candidate.batch_id, candidate.request_id),
        ).fetchone():
            raise ValueError('successor batch or request already prepared under another copy')
        volume.execute('''
            INSERT INTO correction_candidates VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0
            )
        ''', (
            candidate.target.copy_id, candidate.batch_id, candidate.request_id,
            parent.volume.site, parent.volume.volume, parent.volume.domain,
            parent.batch_id, parent.copy_id,
            candidate.parent_receipt.receipt_id, candidate.parent_receipt.seal.row_count,
            candidate.parent_receipt.seal.digest,
            integer_text(candidate.parent_receipt.total_millilitres),
            candidate.before.station, integer_text(candidate.before.row_number),
            None if candidate.before.millilitres is None
            else integer_text(candidate.before.millilitres),
            candidate.before.source.document, integer_text(candidate.before.source.line),
            candidate.correction_source.document,
            integer_text(candidate.correction_source.line),
            candidate.row_count, candidate.digest,
            integer_text(candidate.total_millilitres),
        ))
        volume.executemany(
            'INSERT INTO correction_candidate_rows VALUES (?, ?, ?, ?, ?, ?, ?)',
            ((candidate.target.copy_id, position, *row)
             for position, row in enumerate(encoded)),
        )
    return candidate


def read_candidate(volume, batch_id, *, expected_copy=None):
    """Real-read a candidate material and verify its stored seal."""
    from custody.corrections import correction_digest

    settings = volume_settings(volume)
    header = volume.execute('''
        SELECT copy_id, request_id, parent_site, parent_volume, parent_domain,
               parent_batch_id, parent_copy_id, parent_receipt_id, parent_row_count,
               parent_digest, parent_total, before_station, before_row, before_reading,
               before_document, before_line, correction_document, correction_line,
               row_count, digest, total, published
        FROM correction_candidates WHERE batch_id=?
    ''', (batch_id,)).fetchone()
    if header is None:
        raise KeyError(batch_id)
    (copy_id, request_id, psite, pvolume, pdomain, pbatch, pcopy, preceipt_id,
     prow_count, pdigest, ptotal, bstation, brow, breading, bdoc, bline,
     cdoc, cline, row_count, digest, total, published) = header
    target = CopyIdentity(settings.identity, batch_id, copy_id)
    if expected_copy is not None and target != expected_copy:
        raise ValueError('wrong volume copy')
    records = volume.execute('''
        SELECT position, station, row_number, reading, document, source_line
        FROM correction_candidate_rows WHERE copy_id=? ORDER BY position
    ''', (copy_id,))
    rows = decode_ordered_rows(records)
    if len(rows) != row_count:
        raise ValueError('incomplete correction candidate')
    parent_copy = CopyIdentity(VolumeIdentity(psite, pvolume, pdomain), pbatch, pcopy)
    parent_receipt = Receipt(preceipt_id, pbatch, FrozenSeal(prow_count, pdigest),
                             decode_integer(ptotal))
    before = ObservationRow(bstation, decode_integer(brow),
                           None if breading is None else decode_integer(breading),
                           SourceReference(bdoc, decode_integer(bline)))
    correction_source = SourceReference(cdoc, decode_integer(cline))
    if correction_digest(rows, parent_copy, parent_receipt, before,
                         correction_source) != digest:
        raise ValueError('correction seal does not match the stored material')
    candidate = CorrectionCandidate(
        target, request_id, parent_copy, parent_receipt, before, correction_source,
        row_count, digest, decode_integer(total), bool(published),
    )
    return candidate, rows


def publish_candidate(volume, batch_id, receipt):
    """Deliver the formal publication basis to a prepared candidate."""
    with transaction(volume, write=True):
        stored = volume.execute(
            'SELECT copy_id, digest, total, published FROM correction_candidates WHERE batch_id=?',
            (batch_id,),
        ).fetchone()
        if stored is None:
            raise KeyError(batch_id)
        copy_id, digest, total, published = stored
        if receipt.batch_id != batch_id or receipt.receipt_id != 'receipt-correction-' + digest:
            raise ValueError('publication basis does not match this candidate')
        if receipt.total_millilitres != decode_integer(total):
            raise ValueError('publication basis disagrees with the candidate total')
        if not published:
            volume.execute(
                'UPDATE correction_candidates SET published=1 WHERE batch_id=?', (batch_id,)
            )
    return CopyIdentity(volume_settings(volume).identity, batch_id, copy_id)


def correction_status(volume, batch_id):
    """Return ``(copy_id, published)`` without materializing rows, or ``None``."""
    volume_settings(volume)
    record = volume.execute(
        'SELECT copy_id, published FROM correction_candidates WHERE batch_id=?', (batch_id,)
    ).fetchone()
    if record is None:
        return None
    return record[0], bool(record[1])
