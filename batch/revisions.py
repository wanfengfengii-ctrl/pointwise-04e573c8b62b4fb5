from dataclasses import dataclass, replace

from archive_store import require_name, transaction
from batch.lineage import (
    ObservationRow, SourceReference, decode_integer, encode_rows, row_identity,
    validate_source_reference,
)
from batch.storage import fetch_revision_rows, store_revision


@dataclass(frozen=True)
class Revision:
    day_id: str
    number: int
    rows: tuple[ObservationRow, ...]
    previous: int | None = None
    corrected_row: tuple[str, int] | None = None
    correction_source: SourceReference | None = None


def check_revision_number(revision):
    if type(revision) is not int or not 1 <= revision <= (1 << 63) - 1:
        raise ValueError('invalid revision number')


def load_revision(database, day_id, revision):
    require_name(day_id, 'day identity')
    check_revision_number(revision)
    record = database.execute('''
        SELECT previous_revision, correction_station, correction_row,
               correction_document, correction_line
        FROM revisions WHERE day_id=? AND revision=?
    ''', (day_id, revision)).fetchone()
    if record is None:
        raise KeyError((day_id, revision))
    previous, station, number, document, line = record
    rows = fetch_revision_rows(database, day_id, revision)
    if previous is None:
        return Revision(day_id, revision, rows)
    return Revision(day_id, revision, rows, previous, (station, decode_integer(number)),
                    SourceReference(document, decode_integer(line)))


def create_revision(database, day_id, rows):
    require_name(day_id, 'day identity')
    rows = tuple(rows)
    encode_rows(rows)
    with transaction(database, write=True):
        if database.execute('SELECT 1 FROM revisions WHERE day_id=?', (day_id,)).fetchone():
            raise ValueError('day already has an initial revision')
        store_revision(database, day_id, 1, rows)
    return 1


def apply_correction(database, day_id, previous, station, number, millilitres, source):
    target = row_identity(station, number)
    validate_source_reference(source)
    if millilitres is not None and type(millilitres) is not int:
        raise ValueError('a correction requires integer millilitres or missing')
    with transaction(database, write=True):
        base = load_revision(database, day_id, previous)
        rows = list(base.rows)
        for position, row in enumerate(rows):
            if (row.station, row.row_number) == target:
                rows[position] = replace(row, millilitres=millilitres)
                break
        else:
            raise KeyError(target)
        latest, = database.execute(
            'SELECT MAX(revision) FROM revisions WHERE day_id=?', (day_id,)
        ).fetchone()
        revision = latest + 1
        check_revision_number(revision)
        store_revision(database, day_id, revision, rows, previous=previous,
                       target=target, source=source)
    return revision
