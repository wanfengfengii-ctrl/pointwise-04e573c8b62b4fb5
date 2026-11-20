from audit.fixity import seal_frozen_rows
from audit.quantity import sum_millilitres
from audit.receipts import Receipt, persist_first_receipt
from custody.catalogue import catalogue_write


def make_receipt(batch):
    seal = seal_frozen_rows(batch)
    return Receipt('receipt-' + seal.digest, batch.batch_id, seal, sum_millilitres(batch.rows))


def issue_first_receipt(catalogue, batch):
    candidate = make_receipt(batch)
    with catalogue_write(catalogue):
        return persist_first_receipt(catalogue, candidate)
