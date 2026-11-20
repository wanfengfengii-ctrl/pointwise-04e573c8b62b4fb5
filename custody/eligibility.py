from contextlib import nullcontext

from archive_store import require_name, transaction
from custody.registry import list_retire_exclusions, list_verified_copies


def eligible_domains(catalogue, batch_id, *, without=None):
    require_name(batch_id, 'batch')
    boundary = nullcontext() if catalogue.in_transaction else transaction(catalogue)
    with boundary:
        verified = list_verified_copies(catalogue, batch_id)
        excluded = list_retire_exclusions(catalogue, batch_id)
        return frozenset(copy.volume.domain for copy in verified
                         if copy not in excluded and copy != without)


def has_dual_domain_custody(catalogue, batch_id, *, without=None):
    return len(eligible_domains(catalogue, batch_id, without=without)) >= 2
