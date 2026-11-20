def sum_millilitres(rows):
    total = 0
    for row in rows:
        reading = row.millilitres
        if reading is None:
            continue
        if type(reading) is not int:
            raise ValueError('expected integer millilitres')
        total += reading
    return total
