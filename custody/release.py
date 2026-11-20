from audit.receipts import load_receipt
from custody.catalogue import catalogue_write, load_release_status, record_release
from custody.eligibility import has_dual_domain_custody


def release(catalogue, batch_id):
    with catalogue_write(catalogue):
        load_receipt(catalogue, batch_id)
        if load_release_status(catalogue, batch_id):
            return True
        if not has_dual_domain_custody(catalogue, batch_id):
            return False
        record_release(catalogue, batch_id)
        return True
