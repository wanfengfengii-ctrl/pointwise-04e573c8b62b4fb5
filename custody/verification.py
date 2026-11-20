from archive_store import store_identity
from audit.receipts import load_receipt
from custody.catalogue import catalogue_write
from custody.registry import record_acceptance, registered_domain, registered_key
from media.readback import read_copy
from media.volume import copy_fields, volume_settings


def verify(catalogue, volume, copy):
    copy_fields(copy)
    settings = volume_settings(volume)
    if settings.identity != copy.volume or settings.catalogue_id != store_identity(catalogue, 'catalogue'):
        raise ValueError('wrong station or volume for verification')
    checked = read_copy(volume, copy.batch_id, expected_copy=copy)
    with catalogue_write(catalogue):
        if registered_domain(catalogue, settings.identity) != copy.volume.domain:
            raise ValueError('verification failure domain does not match the copy')
        if registered_key(catalogue, settings.identity) != settings.permit_key:
            raise ValueError('station binding differs from the catalogue')
        if checked.receipt != load_receipt(catalogue, copy.batch_id):
            raise ValueError('volume does not match the catalogue first receipt')
        return record_acceptance(catalogue, copy)
