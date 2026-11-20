from dataclasses import dataclass

from audit.fixity import FrozenSeal
from batch.lineage import decode_integer, integer_text


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    batch_id: str
    seal: FrozenSeal
    total_millilitres: int


def initialize_receipts(database):
    database.execute('''CREATE TABLE IF NOT EXISTS receipts (
        batch_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE,
        row_count INTEGER NOT NULL, digest TEXT NOT NULL, total TEXT NOT NULL
    )''')


def receipt_fields(receipt):
    return (receipt.receipt_id, receipt.seal.row_count, receipt.seal.digest,
            integer_text(receipt.total_millilitres))


def receipt_from_fields(batch_id, fields):
    receipt_id, row_count, digest, total = fields
    return Receipt(receipt_id, batch_id, FrozenSeal(row_count, digest), decode_integer(total))


def load_receipt(database, batch_id):
    fields = database.execute(
        'SELECT receipt_id, row_count, digest, total FROM receipts WHERE batch_id=?', (batch_id,)
    ).fetchone()
    if fields is None:
        raise KeyError(batch_id)
    return receipt_from_fields(batch_id, fields)


def persist_first_receipt(database, receipt):
    database.execute('''
        INSERT INTO receipts VALUES (?, ?, ?, ?, ?) ON CONFLICT(batch_id) DO NOTHING
    ''', (receipt.batch_id, *receipt_fields(receipt)))
    original = load_receipt(database, receipt.batch_id)
    if original != receipt:
        raise ValueError('frozen batch disagrees with its original receipt')
    return original
