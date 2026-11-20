from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


def connect(path, *, create=False):
    mode = 'rwc' if create else 'rw'
    database = sqlite3.connect(
        f'{Path(path).absolute().as_uri()}?mode={mode}',
        uri=True, isolation_level=None, timeout=5,
    )
    try:
        database.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        database.close()
        raise
    return database


@contextmanager
def transaction(database, *, write=False):
    database.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield database
        database.commit()
    except BaseException:
        database.rollback()
        raise


def require_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError(f'{label} must be valid UTF-8') from error
    return value


def store_identity(database, role):
    table = database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_identity'"
    ).fetchone()
    if table is None:
        raise ValueError('uninitialized archive database')
    record = database.execute('SELECT role, name FROM archive_identity WHERE slot=1').fetchone()
    if record is None or record[0] != role:
        raise ValueError(f'expected a {role} archive database')
    return record[1]


def open_store(path, role):
    database = connect(path)
    try:
        store_identity(database, role)
    except BaseException:
        database.close()
        raise
    return database


def initialize_store(path, role, name, prepare):
    require_name(name, f'{role} identity')
    with closing(connect(path, create=True)) as database, transaction(database, write=True):
        database.execute('''CREATE TABLE IF NOT EXISTS archive_identity (
            slot INTEGER PRIMARY KEY CHECK(slot=1), role TEXT NOT NULL, name TEXT NOT NULL
        )''')
        database.execute('INSERT OR IGNORE INTO archive_identity VALUES (1, ?, ?)', (role, name))
        if store_identity(database, role) != name:
            raise ValueError(f'{role} identity differs from the initialized database')
        prepare(database)
