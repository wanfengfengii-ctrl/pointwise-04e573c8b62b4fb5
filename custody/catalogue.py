from contextlib import contextmanager

from archive_store import initialize_store, open_store, store_identity, transaction
from audit.receipts import initialize_receipts
from retire.requests import initialize_requests


_CUSTODY_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS registered_volumes (
        site TEXT NOT NULL, volume TEXT NOT NULL, domain TEXT NOT NULL, permit_key BLOB NOT NULL,
        PRIMARY KEY(site, volume)
    )''',
    '''CREATE TABLE IF NOT EXISTS verified_copies (
        batch_id TEXT NOT NULL REFERENCES receipts(batch_id), site TEXT NOT NULL,
        volume TEXT NOT NULL, copy_id TEXT NOT NULL,
        PRIMARY KEY(batch_id, site, volume, copy_id),
        FOREIGN KEY(site, volume) REFERENCES registered_volumes(site, volume)
    )''',
    '''CREATE TABLE IF NOT EXISTS retire_exclusions (
        batch_id TEXT NOT NULL, site TEXT NOT NULL, volume TEXT NOT NULL, copy_id TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE REFERENCES retire_requests(request_id),
        PRIMARY KEY(batch_id, site, volume, copy_id),
        FOREIGN KEY(batch_id, site, volume, copy_id)
            REFERENCES verified_copies(batch_id, site, volume, copy_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS source_releases (
        batch_id TEXT PRIMARY KEY REFERENCES receipts(batch_id)
    )''',
)


def initialize_catalogue(path, catalogue_id):
    def prepare(database):
        initialize_receipts(database)
        initialize_requests(database)
        from custody.corrections import initialize_correction_requests
        initialize_correction_requests(database)
        for statement in _CUSTODY_SCHEMA:
            database.execute(statement)

    initialize_store(path, 'catalogue', catalogue_id, prepare)


def open_catalogue(path):
    database = open_store(path, 'catalogue')
    from custody.corrections import initialize_correction_requests
    initialize_correction_requests(database)
    return database


@contextmanager
def catalogue_write(database):
    store_identity(database, 'catalogue')
    with transaction(database, write=True):
        yield database


def load_release_status(database, batch_id):
    return database.execute('SELECT 1 FROM source_releases WHERE batch_id=?', (batch_id,)).fetchone() is not None


def record_release(database, batch_id):
    database.execute('INSERT INTO source_releases VALUES (?) ON CONFLICT(batch_id) DO NOTHING', (batch_id,))
