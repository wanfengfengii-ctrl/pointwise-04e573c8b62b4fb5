from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from archive_store import require_name


@dataclass(frozen=True)
class SourceReference:
    document: str
    line: int


@dataclass(frozen=True)
class ObservationRow:
    station: str
    row_number: int
    millilitres: int | None
    source: SourceReference


def validate_source_reference(source):
    if not isinstance(source, SourceReference):
        raise ValueError('a source reference is required')
    require_name(source.document, 'source document')
    if type(source.line) is not int or source.line < 1:
        raise ValueError('source line must be a positive integer')
    return source


def row_identity(station, row_number):
    require_name(station, 'station')
    if type(row_number) is not int or row_number < 1:
        raise ValueError('row number must be a positive integer')
    return station, row_number


def integer_text(value):
    if type(value) is not int:
        raise ValueError('expected an integer')
    return format(Decimal(value), 'f')


def decode_integer(text):
    if not isinstance(text, str):
        raise ValueError('stored integer must be decimal text')
    digits = text.removeprefix('-')
    if not digits or not digits.isascii() or not digits.isdigit():
        raise ValueError('invalid stored integer')
    try:
        value = int(Decimal(text))
    except InvalidOperation as error:
        raise ValueError('invalid stored integer') from error
    if integer_text(value) != text:
        raise ValueError('stored integer is not canonical')
    return value


def encode_row(row):
    if not isinstance(row, ObservationRow):
        raise ValueError('expected an observation row')
    station, number = row_identity(row.station, row.row_number)
    source = validate_source_reference(row.source)
    value = None if row.millilitres is None else integer_text(row.millilitres)
    return station, integer_text(number), value, source.document, integer_text(source.line)


def encode_rows(rows):
    encoded = []
    seen = set()
    for row in rows:
        fields = encode_row(row)
        identity = fields[:2]
        if identity in seen:
            raise ValueError('duplicate station row in revision')
        seen.add(identity)
        encoded.append(fields)
    return tuple(encoded)


def decode_ordered_rows(records):
    rows = []
    for expected, record in enumerate(records):
        position, station, number, value, document, line = record
        if position != expected:
            raise ValueError('stored observation positions are discontinuous')
        rows.append(ObservationRow(
            station, decode_integer(number), None if value is None else decode_integer(value),
            SourceReference(document, decode_integer(line)),
        ))
    encode_rows(rows)
    return tuple(rows)
