from archive_store import store_identity
from custody.catalogue import catalogue_write
from custody.registry import registered_key
from media.withdrawal import ExitResult, validate_exit_result
from retire.requests import load_request, record_terminal


def reconcile(catalogue, result):
    if not isinstance(result, ExitResult):
        raise ValueError('a station exit result is required')
    with catalogue_write(catalogue):
        catalogue_id = store_identity(catalogue, 'catalogue')
        old = load_request(catalogue, result.request_id)
        if old is None or old.permit_signature is None:
            raise ValueError('exit result has no matching issued permit')
        if (result.catalogue_id != catalogue_id or result.copy != old.request.copy
                or result.permit_signature != old.permit_signature):
            raise ValueError('exit result conflicts with the issued permit')
        validate_exit_result(registered_key(catalogue, result.copy.volume), result)
        if old.terminal_signature is not None:
            if old.terminal_signature != result.signature:
                raise ValueError('exit result conflicts with the first settlement')
            return result
        record_terminal(catalogue, result.request_id, result.signature)
        return result
