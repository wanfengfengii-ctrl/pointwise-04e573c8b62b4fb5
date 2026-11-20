from batch.lineage import encode_row
from batch.revisions import apply_correction, create_revision


def parse_reading(value, unit='mL'):
    if unit != 'mL':
        raise ValueError('observation unit must be mL')
    if value is not None and type(value) is not int:
        raise ValueError('observation must be an integer or None')
    return value


def intake(database, day_id, rows=None, *, unit='mL', previous_revision=None, correction=None):
    parse_reading(None, unit)
    if previous_revision is None:
        if rows is None or correction is not None:
            raise ValueError('initial intake requires observation rows')
        rows = tuple(rows)
        for row in rows:
            encode_row(row)
        return create_revision(database, day_id, rows)
    if rows is not None or not isinstance(correction, tuple) or len(correction) != 4:
        raise ValueError('correction requires station, row number, reading and source')
    station, number, value, source = correction
    return apply_correction(database, day_id, previous_revision, station, number,
                            parse_reading(value, unit), source)
