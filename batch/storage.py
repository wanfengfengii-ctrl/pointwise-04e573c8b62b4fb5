from archive_store import initialize_store, open_store
from batch.lineage import decode_ordered_rows, encode_rows, integer_text


_SOURCE_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS revisions (
        day_id TEXT NOT NULL, revision INTEGER NOT NULL, previous_revision INTEGER,
        correction_station TEXT, correction_row TEXT,
        correction_document TEXT, correction_line TEXT,
        PRIMARY KEY (day_id, revision),
        FOREIGN KEY (day_id, previous_revision) REFERENCES revisions(day_id, revision)
    )''',
    '''CREATE TABLE IF NOT EXISTS revision_rows (
        day_id TEXT NOT NULL, revision INTEGER NOT NULL, position INTEGER NOT NULL,
        station TEXT NOT NULL, row_number TEXT NOT NULL, reading TEXT,
        document TEXT NOT NULL, source_line TEXT NOT NULL,
        PRIMARY KEY (day_id, revision, position),
        FOREIGN KEY (day_id, revision) REFERENCES revisions(day_id, revision)
    )''',
    '''CREATE TABLE IF NOT EXISTS frozen_batches (
        batch_id TEXT PRIMARY KEY, day_id TEXT NOT NULL, revision INTEGER NOT NULL,
        row_count INTEGER NOT NULL CHECK(row_count >= 0), UNIQUE(day_id, revision),
        FOREIGN KEY (day_id, revision) REFERENCES revisions(day_id, revision)
    )''',
    '''CREATE TABLE IF NOT EXISTS frozen_rows (
        batch_id TEXT NOT NULL REFERENCES frozen_batches(batch_id), position INTEGER NOT NULL,
        station TEXT NOT NULL, row_number TEXT NOT NULL, reading TEXT,
        document TEXT NOT NULL, source_line TEXT NOT NULL,
        PRIMARY KEY (batch_id, position)
    )''',
)


def initialize_source_store(path, source_id):
    def prepare(database):
        for statement in _SOURCE_SCHEMA:
            database.execute(statement)

    initialize_store(path, 'source', source_id, prepare)


def open_source_store(path):
    return open_store(path, 'source')


def store_revision(database, day_id, revision, rows, *, previous=None, target=None, source=None):
    fields = encode_rows(rows)
    correction = (None, None, None, None)
    if target is not None:
        correction = target[0], integer_text(target[1]), source.document, integer_text(source.line)
    database.execute(
        'INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?)',
        (day_id, revision, previous, *correction),
    )
    database.executemany(
        'INSERT INTO revision_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        ((day_id, revision, position, *row) for position, row in enumerate(fields)),
    )


def fetch_revision_rows(database, day_id, revision):
    records = database.execute('''
        SELECT position, station, row_number, reading, document, source_line
        FROM revision_rows WHERE day_id=? AND revision=? ORDER BY position
    ''', (day_id, revision))
    return decode_ordered_rows(records)


def store_frozen_snapshot(database, batch_id, day_id, revision, rows):
    fields = encode_rows(rows)
    database.execute('INSERT INTO frozen_batches VALUES (?, ?, ?, ?)',
                     (batch_id, day_id, revision, len(fields)))
    database.executemany(
        'INSERT INTO frozen_rows VALUES (?, ?, ?, ?, ?, ?, ?)',
        ((batch_id, position, *row) for position, row in enumerate(fields)),
    )


def fetch_frozen_snapshot(database, batch_id):
    header = database.execute(
        'SELECT day_id, revision, row_count FROM frozen_batches WHERE batch_id=?', (batch_id,)
    ).fetchone()
    if header is None:
        raise KeyError(batch_id)
    day_id, revision, count = header
    records = database.execute('''
        SELECT position, station, row_number, reading, document, source_line
        FROM frozen_rows WHERE batch_id=? ORDER BY position
    ''', (batch_id,))
    rows = decode_ordered_rows(records)
    if len(rows) != count:
        raise ValueError('incomplete frozen snapshot')
    return day_id, revision, rows
