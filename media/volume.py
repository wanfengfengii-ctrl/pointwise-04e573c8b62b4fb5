from dataclasses import dataclass
import json

from archive_store import initialize_store, open_store, require_name, store_identity


@dataclass(frozen=True)
class VolumeIdentity:
    site: str
    volume: str
    domain: str


@dataclass(frozen=True)
class CopyIdentity:
    volume: VolumeIdentity
    batch_id: str
    copy_id: str


@dataclass(frozen=True)
class VolumeSettings:
    identity: VolumeIdentity
    catalogue_id: str
    permit_key: bytes


_VOLUME_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS volume_binding (
        slot INTEGER PRIMARY KEY CHECK(slot=1), site TEXT NOT NULL, volume TEXT NOT NULL,
        domain TEXT NOT NULL, catalogue_id TEXT NOT NULL, permit_key BLOB NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS copies (
        copy_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE,
        source_id TEXT NOT NULL, day_id TEXT NOT NULL, revision INTEGER NOT NULL,
        row_count INTEGER NOT NULL, digest TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS copy_rows (
        copy_id TEXT NOT NULL REFERENCES copies(copy_id) ON DELETE CASCADE,
        position INTEGER NOT NULL, station TEXT NOT NULL, row_number TEXT NOT NULL,
        reading TEXT, document TEXT NOT NULL, source_line TEXT NOT NULL,
        PRIMARY KEY(copy_id, position)
    )''',
    '''CREATE TABLE IF NOT EXISTS copy_receipts (
        copy_id TEXT PRIMARY KEY REFERENCES copies(copy_id) ON DELETE CASCADE,
        receipt_id TEXT NOT NULL, row_count INTEGER NOT NULL, digest TEXT NOT NULL, total TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS copy_exits (
        request_id TEXT PRIMARY KEY, copy_id TEXT NOT NULL UNIQUE, batch_id TEXT NOT NULL,
        permit_signature TEXT NOT NULL, signature TEXT NOT NULL
    )''',
)


def volume_fields(identity):
    if not isinstance(identity, VolumeIdentity):
        raise ValueError('expected a volume identity')
    return (require_name(identity.site, 'site'), require_name(identity.volume, 'volume'),
            require_name(identity.domain, 'failure domain'))


def copy_fields(identity):
    if not isinstance(identity, CopyIdentity):
        raise ValueError('expected a copy identity')
    return (*volume_fields(identity.volume), require_name(identity.batch_id, 'batch'),
            require_name(identity.copy_id, 'copy'))


def volume_settings(database):
    name = store_identity(database, 'volume')
    row = database.execute('''
        SELECT site, volume, domain, catalogue_id, permit_key FROM volume_binding WHERE slot=1
    ''').fetchone()
    if row is None:
        raise ValueError('volume has no binding')
    site, volume, domain, catalogue_id, key = row
    if json.dumps([site, volume], ensure_ascii=True) != name:
        raise ValueError('volume store identity mismatch')
    return VolumeSettings(VolumeIdentity(site, volume, domain), catalogue_id, key)


def initialize_volume(path, identity, catalogue_id, permit_key):
    fields = volume_fields(identity)
    require_name(catalogue_id, 'catalogue identity')
    if type(permit_key) is not bytes or len(permit_key) < 32:
        raise ValueError('permit key requires at least 32 bytes')

    def prepare(database):
        for statement in _VOLUME_SCHEMA:
            database.execute(statement)
        from media.corrections import initialize_corrections
        initialize_corrections(database)
        database.execute('INSERT OR IGNORE INTO volume_binding VALUES (1, ?, ?, ?, ?, ?)',
                         (*fields, catalogue_id, permit_key))
        if volume_settings(database) != VolumeSettings(identity, catalogue_id, permit_key):
            raise ValueError('volume configuration differs from the initialized binding')

    name = json.dumps(list(fields[:2]), ensure_ascii=True)
    initialize_store(path, 'volume', name, prepare)


def open_volume(path):
    database = open_store(path, 'volume')
    try:
        volume_settings(database)
        from media.corrections import initialize_corrections
        initialize_corrections(database)
    except BaseException:
        database.close()
        raise
    return database


def locate_batch_copy(database, batch_id):
    require_name(batch_id, 'batch')
    identity = volume_settings(database).identity
    found = database.execute('SELECT copy_id FROM copies WHERE batch_id=?', (batch_id,)).fetchone()
    if found is None:
        raise KeyError(batch_id)
    return CopyIdentity(identity, batch_id, found[0])
