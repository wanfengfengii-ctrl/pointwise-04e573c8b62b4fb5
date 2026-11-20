from dataclasses import replace
import sqlite3

from audit.receipts import load_receipt
from audit.retrieve import read
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.revisions import load_revision
from batch.storage import initialize_source_store, open_source_store
from custody.catalogue import initialize_catalogue, open_catalogue
from custody.eligibility import eligible_domains
from custody.registry import registered_domain
from custody.release import release
from custody.verification import verify
from media.replicate import replicate
from media.volume import initialize_volume, locate_batch_copy, open_volume, volume_settings
from media.withdrawal import load_exit_result
from retire.execution import execute
from retire.permits import permit
from retire.reconciliation import reconcile
from tests.controls import reject_copy_receipt_write, replace_copy_source
from tests.fixtures import ArchiveCase


class PreservationEdgeTests(ArchiveCase):
    def test_offline_receipt(self):
        self.seed()
        self.archive('A')
        copy = self.archive('B')
        self.assertTrue(release(self.catalogue, self.batch.batch_id))
        self.source.close()
        self.catalogue.close()
        self.volumes['B'].close()
        station = self.own(open_volume(self.volume_path('B')))
        self.assertEqual(read(self.batch.batch_id, volume=station, expected_copy=copy), self.report)

    def test_partial_write_rollback(self):
        self.seed()
        self.archive('A')
        volume = self.volume('B')
        with reject_copy_receipt_write(volume):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'copy receipt write rejected'):
                replicate(self.report, volume, 'copy-B')
            with self.assertRaises(KeyError):
                locate_batch_copy(volume, self.batch.batch_id)
            self.assertEqual(volume.execute('SELECT COUNT(*) FROM copy_rows').fetchone(), (0,))
        copy = replicate(self.report, volume, 'copy-B')
        self.assertEqual(verify(self.catalogue, volume, copy), copy)
        self.assertTrue(release(self.catalogue, self.batch.batch_id))
        self.source.close()
        self.catalogue.close()
        self.assertEqual(read(self.batch.batch_id, volume=volume), self.report)

    def test_same_length_lineage(self):
        self.seed()
        self.archive('A')
        self.archive('A2', 'A')
        copy = self.archive('B', accept=False)
        replace_copy_source(self.volumes['B'], copy, 'station-other!')
        with self.assertRaises(ValueError):
            verify(self.catalogue, self.volumes['B'], copy)
        with self.assertRaises(ValueError):
            read(self.batch.batch_id, volume=self.volumes['B'])
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'A'})
        self.assertFalse(release(self.catalogue, self.batch.batch_id))
        self.archive('C')
        self.assertTrue(release(self.catalogue, self.batch.batch_id))

    def test_wrong_copy_identity(self):
        self.seed()
        copy = self.archive('B', accept=False)
        with self.assertRaises(ValueError):
            verify(self.catalogue, self.volumes['B'], replace(copy, copy_id='wrong'))
        wrong_domain = replace(copy.volume, domain='different-domain')
        with self.assertRaises(ValueError):
            verify(self.catalogue, self.volumes['B'], replace(copy, volume=wrong_domain))
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), set())

    def test_unverified_other_domain(self):
        self.seed()
        self.archive('A')
        copy = self.archive('B', accept=False)
        self.assertFalse(release(self.catalogue, self.batch.batch_id))
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'A'})
        verify(self.catalogue, self.volumes['B'], copy)
        self.assertTrue(release(self.catalogue, self.batch.batch_id))

    def test_init_reopen(self):
        self.seed()
        for name in 'ABC':
            self.archive(name)
        self.assertTrue(release(self.catalogue, self.batch.batch_id))
        extra_rows = (ObservationRow('estuary', 1, 79, SourceReference('late-ledger', 6)),)
        revision = intake(self.source, 'another-day', extra_rows)
        request = self.request('A')
        decision = permit(self.catalogue, request)
        terminal = execute(self.volumes['A'], decision.permit)
        self.assertEqual(reconcile(self.catalogue, terminal), terminal)
        settings = volume_settings(self.volumes['A'])
        self.volumes['A'].close()
        self.catalogue.close()
        self.source.close()
        initialize_source_store(self.source_path, 'basin-stations')
        initialize_catalogue(self.catalogue_path, 'basin-archive')
        initialize_volume(self.volume_path('A'), settings.identity, settings.catalogue_id, settings.permit_key)
        self.source = self.own(open_source_store(self.source_path))
        self.catalogue = self.own(open_catalogue(self.catalogue_path))
        volume = self.own(open_volume(self.volume_path('A')))
        self.assertEqual(load_revision(self.source, 'another-day', revision).rows, extra_rows)
        self.assertEqual(load_receipt(self.catalogue, self.batch.batch_id), self.report.receipt)
        self.assertEqual(registered_domain(self.catalogue, self.copies['B'].volume), 'B')
        self.assertEqual(load_exit_result(volume, request.request_id), terminal)
        self.assertEqual(permit(self.catalogue, request).terminal, terminal)
        with self.assertRaises(ValueError):
            initialize_catalogue(self.catalogue_path, 'different-catalogue')
        self.assertEqual(read(self.batch.batch_id, volume=self.volumes['C']), self.report)
