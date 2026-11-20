from contextlib import contextmanager


def remove_last_copy_row(volume, copy):
    volume.execute('''
        DELETE FROM copy_rows WHERE copy_id=? AND position=(
            SELECT MAX(position) FROM copy_rows WHERE copy_id=?
        )
    ''', (copy.copy_id, copy.copy_id))


def replace_copy_source(volume, copy, document):
    volume.execute('UPDATE copy_rows SET document=? WHERE copy_id=? AND position=0',
                   (document, copy.copy_id))


@contextmanager
def reject_copy_receipt_write(volume):
    volume.execute('''CREATE TRIGGER reject_copy_receipt BEFORE INSERT ON copy_receipts
        BEGIN SELECT RAISE(ABORT, 'copy receipt write rejected'); END''')
    try:
        yield
    finally:
        volume.execute('DROP TRIGGER reject_copy_receipt')
