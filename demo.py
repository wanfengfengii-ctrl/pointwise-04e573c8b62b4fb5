import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory

from audit.retrieve import read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.storage import initialize_source_store, open_source_store
from correction_cases import run_correction_handover
from custody.catalogue import initialize_catalogue, open_catalogue
from custody.eligibility import eligible_domains
from custody.registry import register_volume_domain
from custody.release import release
from custody.verification import verify
from media.replicate import replicate
from media.volume import VolumeIdentity, open_volume, initialize_volume
from retire.execution import execute
from retire.permits import permit
from retire.reconciliation import reconcile
from retire.requests import RetireRequest


class BusinessFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise BusinessFailure(code)


def build_day(directory):
    directory.mkdir()
    source_path = directory / 'source.sqlite'
    catalogue_path = directory / 'catalogue.sqlite'
    initialize_source_store(source_path, 'estuary-gauges')
    initialize_catalogue(catalogue_path, 'basin-archive')
    with ExitStack() as stations:
        with closing(open_source_store(source_path)) as source, closing(open_catalogue(catalogue_path)) as catalogue:
            day = '2026-10-04'
            observations = (
                ObservationRow('lower-estuary', 1, 7000, SourceReference('east-gauge', 17)),
                ObservationRow('lower-estuary', 2, None, SourceReference('east-gauge', 18)),
                ObservationRow('upper-estuary', 1, 11000, SourceReference('west-gauge', 9)),
            )
            revision = intake(source, day, observations)
            batch = freeze(source, day, revision)
            original = read(batch.batch_id, source=source, catalogue=catalogue)
            later = intake(source, day, previous_revision=revision,
                           correction=('upper-estuary', 1, 12000, SourceReference('shift-correction', 1)))
            later_batch = freeze(source, day, later)
            amended = read(later_batch.batch_id, source=source, catalogue=catalogue)
            require(amended.total_millilitres == 19000, 'AMENDED_QUANTITY_MISMATCH')
            volumes = {}
            copies = {}

            def archive(name, report, domain):
                path = directory / f'{name}.sqlite'
                identity = VolumeIdentity(f'archive-site-{name}', 'day-volume', domain)
                initialize_volume(path, identity, 'basin-archive', token_bytes(32))
                volume = stations.enter_context(closing(open_volume(path)))
                register_volume_domain(catalogue, volume)
                copy = replicate(report, volume, f'copy-{name}')
                verify(catalogue, volume, copy)
                volumes[name] = volume
                copies[name] = copy

            for name in 'ABC':
                archive(name, original, name)
            batch_id = batch.batch_id
            require(release(catalogue, batch_id), 'SOURCE_RELEASE_REJECTED')
            source.close()
            first = permit(catalogue, RetireRequest('rotate-A', batch_id, copies['A']))
            require(first.status == 'permitted', 'FIRST_ROTATION_REJECTED')
            trial = RetireRequest('rotate-B-before-D', batch_id, copies['B'])
            before = permit(catalogue, trial)
            require(before.status == 'rejected' and before.permit is None, 'CUSTODY_FLOOR_BREACH')
            archive('D', read(batch_id, volume=volumes['C']), 'D')
            require(permit(catalogue, trial) == before, 'REJECTED_REQUEST_REOPENED')
            second = permit(catalogue, RetireRequest('rotate-B-with-D', batch_id, copies['B']))
            require(second.status == 'permitted', 'REPLACEMENT_NOT_RECOGNIZED')
        execute(volumes['A'], first.permit)
        b_result = execute(volumes['B'], second.permit)
        a_result = execute(volumes['A'], first.permit)
        with closing(open_catalogue(catalogue_path)) as catalogue:
            for result in (a_result, b_result):
                require(reconcile(catalogue, result) == result, 'EXIT_SETTLEMENT_MISMATCH')
            require(eligible_domains(catalogue, batch_id) == {'C', 'D'}, 'WRONG_SURVIVING_DOMAINS')
        for name in 'CD':
            require(read(batch_id, volume=volumes[name]) == original, 'OFFLINE_ARCHIVE_MISMATCH')
        (directory / 'batch.txt').write_text(batch_id, encoding='ascii')


def show_day(directory):
    batch_id = (directory / 'batch.txt').read_text(encoding='ascii')
    records = []
    for name in 'CD':
        with closing(open_volume(directory / f'{name}.sqlite')) as volume:
            report = read(batch_id, volume=volume)
        records.append({
            'domain': name,
            'values': [row.millilitres for row in report.rows],
            'sources': [[row.source.document, row.source.line] for row in report.rows],
            'total_millilitres': report.total_millilitres,
            'receipt_id': report.receipt.receipt_id,
        })
    values = [7000, None, 11000]
    sources = [['east-gauge', 17], ['east-gauge', 18], ['west-gauge', 9]]
    for record in records:
        require(record['values'] == values and record['sources'] == sources and
                record['total_millilitres'] == 18000, 'OFFLINE_ARCHIVE_MISMATCH')
    require(records[0]['receipt_id'] == records[1]['receipt_id'], 'FIRST_RECEIPT_MISMATCH')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v7',
        'input': {'millilitres': values, 'accepted_domains': ['A', 'B', 'C'], 'replacement': 'D'},
        'expected': {'surviving_domains': ['C', 'D']},
        'actual': {'surviving_domains': [record['domain'] for record in records]},
        'downstream_expected': {'values': values, 'sources': sources, 'total_millilitres': 18000},
        'downstream_actual': records,
        'code_paths': ['custody/eligibility.py', 'retire/permits.py', 'media/withdrawal.py',
                       'retire/reconciliation.py', 'audit/retrieve.py', 'audit/quantity.py'],
        'oracle': '7000 + 11000 = 18000 mL; the middle missing row and all input sources stay in place',
    }, sort_keys=True))


def run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    day = directory / 'sample-day'
    if not day.exists():
        with TemporaryDirectory(dir=directory) as staging:
            candidate = Path(staging) / 'day'
            build_day(candidate)
            try:
                candidate.rename(day)
            except OSError:
                if not day.is_dir():
                    raise
    show_day(day)


def run_correction_demo(directory):
    try:
        run_correction_handover(directory)
    except AssertionError as error:
        raise BusinessFailure(f'CORRECTION_HANDOVER_MISMATCH {error}') from error


def main():
    parser = argparse.ArgumentParser(description='Run the basin archive sample day')
    parser.add_argument('--directory', type=Path, help='keep the sample day under this work directory')
    arguments = parser.parse_args()
    try:
        if arguments.directory is None:
            with TemporaryDirectory() as directory:
                run(Path(directory))
            with TemporaryDirectory() as correction_directory:
                run_correction_demo(Path(correction_directory))
        else:
            run(arguments.directory)
            run_correction_demo(arguments.directory / 'correction-day')
    except BusinessFailure as error:
        print(str(error))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
