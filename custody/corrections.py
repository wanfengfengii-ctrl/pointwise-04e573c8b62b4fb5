"""Late corrections for already released V1 batches.

A correction is handled without ever touching the heat source: the parent
material is real-read from a surviving verified volume, a corrected candidate
is prepared on two volumes in distinct failure domains, and only after both
targets accept the *same* candidate does the catalogue fix the successor
identity and its formal first receipt.  Each target still needs the formal
publication basis delivered to its own station database before it can serve
the new ledger offline.
"""
import hashlib
import json

from archive_store import require_name, store_identity, transaction
from audit.fixity import FrozenSeal
from audit.quantity import sum_millilitres
from audit.receipts import Receipt, load_receipt, persist_first_receipt
from batch.lineage import (
    ObservationRow, decode_integer, encode_row, integer_text,
    row_identity, validate_source_reference,
)
from custody.catalogue import catalogue_write, load_release_status
from custody.registry import (
    list_retire_exclusions, list_verified_copies, registered_domain, registered_key,
)
from media.corrections import (
    CorrectionCandidate, correction_status, prepare_candidate, publish_candidate,
    read_candidate,
)
from media.readback import read_copy
from media.volume import CopyIdentity, VolumeIdentity, copy_fields, volume_settings


_CORRECTION_REQUEST_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS correction_requests (
        request_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
        parent_batch_id TEXT NOT NULL, parent_receipt_id TEXT NOT NULL,
        parent_site TEXT NOT NULL, parent_volume TEXT NOT NULL, parent_domain TEXT NOT NULL,
        parent_copy_id TEXT NOT NULL,
        station TEXT NOT NULL, row_number TEXT NOT NULL, millilitres TEXT,
        correction_document TEXT NOT NULL, correction_line TEXT NOT NULL,
        digest TEXT NOT NULL, total TEXT NOT NULL, published INTEGER NOT NULL
            CHECK(published IN (0, 1))
    )''',
    '''CREATE TABLE IF NOT EXISTS correction_targets (
        request_id TEXT NOT NULL REFERENCES correction_requests(request_id),
        position INTEGER NOT NULL, site TEXT NOT NULL, volume TEXT NOT NULL,
        domain TEXT NOT NULL, copy_id TEXT NOT NULL,
        PRIMARY KEY(request_id, position), UNIQUE(site, volume, copy_id)
    )''',
)


def initialize_correction_requests(database):
    for statement in _CORRECTION_REQUEST_SCHEMA:
        database.execute(statement)


def correction_digest(rows, parent_copy, parent_receipt, before, correction_source):
    """Seal the full corrected text together with its correction lineage."""
    encoded_rows = tuple(encode_row(row) for row in rows)
    record = [
        'correction-v1',
        encoded_rows,
        list(copy_fields(parent_copy)),
        parent_receipt.receipt_id,
        [parent_receipt.seal.row_count, parent_receipt.seal.digest,
         integer_text(parent_receipt.total_millilitres)],
        list(encode_row(before)),
        [correction_source.document, integer_text(correction_source.line)],
    ]
    content = json.dumps(record, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(content).hexdigest()


def successor_batch_id(catalogue_id, parent_batch_id, request_id):
    require_name(request_id, 'correction request')
    key = json.dumps(
        ['correction-successor-v1', catalogue_id, parent_batch_id, request_id],
        separators=(',', ':'), ensure_ascii=True,
    ).encode('ascii')
    return 'correction-' + hashlib.sha256(key).hexdigest()


def _parse_correction(correction):
    if not isinstance(correction, tuple) or len(correction) != 4:
        raise ValueError('correction requires station, row number, reading and source')
    station, number, millilitres, source = correction
    row_identity(station, number)
    if millilitres is not None and type(millilitres) is not int:
        raise ValueError('a correction requires integer millilitres or missing')
    validate_source_reference(source)
    return station, number, millilitres, source


def _parse_targets(targets):
    parsed = tuple(targets)
    if len(parsed) != 2:
        raise ValueError('a correction needs exactly two target volumes')
    result = []
    seen_volumes = set()
    for entry in parsed:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError('each target must be a (volume, copy_id) pair')
        volume, copy_id = entry
        settings = volume_settings(volume)
        require_name(copy_id, 'correction copy')
        if settings.identity in seen_volumes:
            raise ValueError('the two target copies must be on different volumes')
        seen_volumes.add(settings.identity)
        result.append((volume, settings, copy_id))
    return tuple(result)


def _load_bound_request(catalogue, request_id):
    record = catalogue.execute('''
        SELECT request_id, batch_id, parent_batch_id, parent_receipt_id, parent_site,
               parent_volume, parent_domain, parent_copy_id, station, row_number,
               millilitres, correction_document, correction_line, digest, total, published
        FROM correction_requests WHERE request_id=?
    ''', (request_id,)).fetchone()
    if record is None:
        return None
    target_rows = catalogue.execute('''
        SELECT position, site, volume, domain, copy_id FROM correction_targets
        WHERE request_id=? ORDER BY position
    ''', (request_id,)).fetchall()
    return record, tuple(target_rows)


def _record_request(catalogue, *, request_id, batch_id, parent_copy, parent_receipt,
                    station, number, millilitres, correction_source, digest, total,
                    target_bindings, published):
    catalogue.execute('''
        INSERT INTO correction_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        request_id, batch_id, parent_copy.batch_id, parent_receipt.receipt_id,
        parent_copy.volume.site, parent_copy.volume.volume, parent_copy.volume.domain,
        parent_copy.copy_id, station, integer_text(number),
        None if millilitres is None else integer_text(millilitres),
        correction_source.document, integer_text(correction_source.line),
        digest, integer_text(total), 1 if published else 0,
    ))
    catalogue.executemany('''
        INSERT INTO correction_targets VALUES (?, ?, ?, ?, ?, ?)
    ''', ((request_id, position, site, volume, domain, copy_id)
          for position, (site, volume, domain, copy_id) in enumerate(target_bindings)))


def _apply_correction(rows, station, number, millilitres):
    target_key = (station, number)
    corrected = list(rows)
    for position, row in enumerate(corrected):
        if (row.station, row.row_number) == target_key:
            before = row
            corrected[position] = ObservationRow(station, number, millilitres, row.source)
            return before, tuple(corrected)
    raise KeyError(target_key)


def _validate_parent(catalogue, parent_volume, parent_copy):
    copy_fields(parent_copy)
    settings = volume_settings(parent_volume)
    catalogue_id = store_identity(catalogue, 'catalogue')
    if settings.catalogue_id != catalogue_id or settings.identity != parent_copy.volume:
        raise ValueError('parent volume binding does not match the catalogue')
    try:
        domain = registered_domain(catalogue, settings.identity)
    except KeyError as error:
        raise ValueError('parent volume is not registered with this catalogue') from error
    if domain != parent_copy.volume.domain:
        raise ValueError('parent copy failure domain does not match its registration')
    if registered_key(catalogue, settings.identity) != settings.permit_key:
        raise ValueError('parent station binding differs from the catalogue')
    parent_batch_id = parent_copy.batch_id
    if not load_release_status(catalogue, parent_batch_id):
        raise ValueError('parent batch has not been released by this catalogue')
    if parent_copy not in list_verified_copies(catalogue, parent_batch_id):
        raise ValueError('parent copy must be a verified copy of the released parent batch')
    if parent_copy in list_retire_exclusions(catalogue, parent_batch_id):
        raise ValueError('parent copy has already been retired')
    checked = read_copy(parent_volume, parent_batch_id, expected_copy=parent_copy)
    parent_receipt = load_receipt(catalogue, parent_batch_id)
    if checked.receipt != parent_receipt:
        raise ValueError('station real-read does not match the catalogue parent receipt')
    return checked, parent_receipt


def _validate_targets(catalogue, catalogue_id, parsed_targets, batch_id):
    target_bindings = []
    domains = set()
    for volume, settings, copy_id in parsed_targets:
        identity = settings.identity
        if settings.catalogue_id != catalogue_id:
            raise ValueError('target volume belongs to a different catalogue')
        try:
            domain = registered_domain(catalogue, identity)
        except KeyError as error:
            raise ValueError('target volume is not registered with this catalogue') from error
        if domain != identity.domain:
            raise ValueError('target failure domain does not match its registration')
        if registered_key(catalogue, identity) != settings.permit_key:
            raise ValueError('target station binding differs from the catalogue')
        if domain in domains:
            raise ValueError('both target volumes must be in independent failure domains')
        domains.add(domain)
        if volume.execute('SELECT 1 FROM copies WHERE batch_id=?',
                          (batch_id,)).fetchone():
            raise ValueError('successor id collides with a V1 batch on a target volume')
        occupied = volume.execute(
            'SELECT batch_id FROM correction_candidates WHERE copy_id=?', (copy_id,)
        ).fetchone()
        if occupied is not None and occupied[0] != batch_id:
            raise ValueError('target copy already holds a different correction candidate')
        target_bindings.append((identity.site, identity.volume, identity.domain, copy_id))
    return tuple(target_bindings)


class _Material:
    def __init__(self, request_id, batch_id, parent_copy, parent_receipt, before,
                 correction_source, corrected_rows, digest, total, target_bindings):
        self.request_id = request_id
        self.batch_id = batch_id
        self.parent_copy = parent_copy
        self.parent_receipt = parent_receipt
        self.before = before
        self.correction_source = correction_source
        self.corrected_rows = corrected_rows
        self.digest = digest
        self.total = total
        self.target_bindings = target_bindings

    @property
    def receipt(self):
        return Receipt('receipt-correction-' + self.digest, self.batch_id,
                       FrozenSeal(len(self.corrected_rows), self.digest), self.total)

    def candidate(self, settings, copy_id):
        target = CopyIdentity(settings.identity, self.batch_id, copy_id)
        return CorrectionCandidate(
            target, self.request_id, self.parent_copy, self.parent_receipt, self.before,
            self.correction_source, len(self.corrected_rows), self.digest, self.total, False,
        )


def _check_replay(catalogue, bound, request_id, catalogue_id, parent_volume, parent_copy,
                  correction, parsed_targets):
    record, target_rows = bound
    (bound_request_id, batch_id, parent_batch_id, parent_receipt_id, psite, pvolume, pdomain,
     pcopy, bound_station, bound_number, bound_ml, cdoc, cline, digest, total_text,
     _published) = record
    if bound_request_id != request_id:
        raise ValueError('reopen request identity mismatch')
    station, number, millilitres, correction_source = correction
    if station != bound_station or integer_text(number) != bound_number:
        raise ValueError('reopen request binds a different corrected row')
    bound_value = None if bound_ml is None else decode_integer(bound_ml)
    if bound_value != millilitres:
        raise ValueError('reopen request binds a different corrected value')
    if (correction_source.document != cdoc
            or integer_text(correction_source.line) != cline):
        raise ValueError('reopen request binds a different correction source')
    bound_parent = CopyIdentity(
        VolumeIdentity(psite, pvolume, pdomain), parent_batch_id, pcopy)
    if parent_copy != bound_parent:
        raise ValueError('reopen request binds a different parent copy')
    settings = volume_settings(parent_volume)
    if settings.identity != bound_parent.volume or settings.catalogue_id != catalogue_id:
        raise ValueError('parent volume binding does not match the bound request')
    if len(parsed_targets) != len(target_rows):
        raise ValueError('reopen request binds a different target set')
    bindings = []
    for (volume, target_settings, copy_id), target_row in zip(parsed_targets, target_rows):
        position, site, name, domain, bound_copy_id = target_row
        identity = target_settings.identity
        if (identity.site, identity.volume, identity.domain, copy_id) != (
                site, name, domain, bound_copy_id):
            raise ValueError('reopen request binds a different target copy')
        if target_settings.catalogue_id != catalogue_id:
            raise ValueError('target volume belongs to a different catalogue')
        bindings.append((site, name, domain, bound_copy_id))
    checked = read_copy(parent_volume, parent_batch_id, expected_copy=parent_copy)
    parent_receipt = load_receipt(catalogue, parent_batch_id)
    if checked.receipt != parent_receipt or parent_receipt.receipt_id != parent_receipt_id:
        raise ValueError('parent real-read no longer matches the bound lineage')
    before, corrected_rows = _apply_correction(
        checked.batch.rows, station, number, millilitres)
    if correction_digest(corrected_rows, parent_copy, parent_receipt, before,
                         correction_source) != digest:
        raise ValueError('recomputed correction material differs from the bound request')
    return _Material(request_id, batch_id, parent_copy, parent_receipt, before,
                     correction_source, corrected_rows, digest, decode_integer(total_text),
                     tuple(bindings))


def _verify_candidate(material, candidate, rows, settings):
    if candidate.target.volume != settings.identity:
        raise ValueError('correction candidate volume binding mismatch')
    if candidate.batch_id != material.batch_id:
        raise ValueError('candidate binds a different successor batch')
    if candidate.parent_copy != material.parent_copy:
        raise ValueError('candidate lineage does not match the parent copy binding')
    if candidate.parent_receipt != material.parent_receipt:
        raise ValueError('candidate lineage does not match the parent first receipt')
    if candidate.before != material.before:
        raise ValueError('candidate did not preserve the original pre-correction row')
    if candidate.correction_source != material.correction_source:
        raise ValueError('candidate did not preserve the correction source')
    if rows != material.corrected_rows:
        raise ValueError('candidate text differs from the accepted correction, row by row')
    if candidate.digest != material.digest or candidate.receipt != material.receipt:
        raise ValueError('candidate seal does not match the accepted correction material')
    if sum_millilitres(rows) != material.total or candidate.total_millilitres != material.total:
        raise ValueError('candidate total does not match its ordered readings')


def publish_correction(catalogue, request_id, parent_volume, parent_copy, correction,
                       targets, *, after_event=None):
    """Publish a late correction successor across two independent failure domains."""
    require_name(request_id, 'correction request')
    catalogue_id = store_identity(catalogue, 'catalogue')
    initialize_correction_requests(catalogue)
    correction = _parse_correction(correction)
    parsed_targets = _parse_targets(targets)

    with transaction(catalogue):
        bound = _load_bound_request(catalogue, request_id)

    if bound is None:
        checked, parent_receipt = _validate_parent(catalogue, parent_volume, parent_copy)
        station, number, millilitres, correction_source = correction
        before, corrected_rows, digest, total = (
            _build_material(checked, parent_receipt, parent_copy, correction))
        batch_id = successor_batch_id(catalogue_id, parent_copy.batch_id, request_id)
        target_bindings = _validate_targets(catalogue, catalogue_id, parsed_targets, batch_id)
        with catalogue_write(catalogue):
            if _load_bound_request(catalogue, request_id) is not None:
                raise ValueError('correction request was concurrently bound')
            _record_request(
                catalogue, request_id=request_id, batch_id=batch_id, parent_copy=parent_copy,
                parent_receipt=parent_receipt, station=station, number=number,
                millilitres=millilitres, correction_source=correction_source, digest=digest,
                total=total, target_bindings=target_bindings, published=False,
            )
        material = _Material(request_id, batch_id, parent_copy, parent_receipt, before,
                             correction_source, corrected_rows, digest, total, target_bindings)
    else:
        material = _check_replay(catalogue, bound, request_id, catalogue_id, parent_volume,
                                 parent_copy, correction, parsed_targets)

    return _drive_stages(catalogue, material, parsed_targets, after_event)


def _build_material(checked, parent_receipt, parent_copy, correction):
    station, number, millilitres, correction_source = correction
    before, corrected_rows = _apply_correction(checked.batch.rows, station, number, millilitres)
    total = sum_millilitres(corrected_rows)
    digest = correction_digest(corrected_rows, parent_copy, parent_receipt, before,
                               correction_source)
    return before, corrected_rows, digest, total


def _drive_stages(catalogue, material, parsed_targets, after_event):
    prepared = []
    for (volume, settings, copy_id) in parsed_targets:
        target = CopyIdentity(settings.identity, material.batch_id, copy_id)
        status = correction_status(volume, material.batch_id)
        if status is None:
            prepare_candidate(volume, material.candidate(settings, copy_id),
                              material.corrected_rows)
            if after_event is not None:
                after_event('prepared', material.batch_id, target)
        elif status[0] != copy_id:
            raise ValueError('successor batch already prepared under a different copy')
        candidate, rows = read_candidate(volume, material.batch_id, expected_copy=target)
        _verify_candidate(material, candidate, rows, settings)
        prepared.append((volume, settings, target))

    with catalogue_write(catalogue):
        record = catalogue.execute(
            'SELECT published FROM correction_requests WHERE batch_id=?',
            (material.batch_id,),
        ).fetchone()
        if record is None:
            raise KeyError(material.batch_id)
        already_published = bool(record[0])
        if not already_published:
            persist_first_receipt(catalogue, material.receipt)
            catalogue.execute(
                'UPDATE correction_requests SET published=1 WHERE batch_id=?',
                (material.batch_id,),
            )
    if load_receipt(catalogue, material.batch_id) != material.receipt:
        raise ValueError('formal correction receipt disagrees with the accepted material')
    if after_event is not None and not already_published:
        after_event('published', material.batch_id, None)

    for volume, settings, target in prepared:
        status = correction_status(volume, material.batch_id)
        if status is None or status[0] != target.copy_id:
            raise ValueError('target lost its prepared correction candidate')
        if not status[1]:
            publish_candidate(volume, material.batch_id, material.receipt)
            if after_event is not None:
                after_event('available', material.batch_id, target)
        candidate, rows = read_candidate(volume, material.batch_id, expected_copy=target)
        if not candidate.published or candidate.receipt != material.receipt:
            raise ValueError('target publication basis is not retrievable offline')
        _verify_candidate(material, candidate, rows, settings)

    return material.batch_id, material.receipt
