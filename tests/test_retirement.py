from dataclasses import replace

from audit.retrieve import read
from custody.catalogue import open_catalogue
from custody.eligibility import eligible_domains
from custody.release import release
from custody.verification import verify
from media.volume import open_volume
from retire.execution import execute
from retire.permits import permit
from retire.reconciliation import reconcile
from tests.fixtures import ArchiveCase


class RetirementTests(ArchiveCase):
    def prepare_rotation(self):
        self.seed((7000, 11000))
        for name in 'ABC':
            self.archive(name)
        self.assertTrue(release(self.catalogue, self.batch.batch_id))

    def test_single_permitted_retirement(self):
        self.prepare_rotation()
        request = self.request('A')
        decision = permit(self.catalogue, request)
        self.assertEqual(decision.status, 'permitted')
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'B', 'C'})
        terminal = execute(self.volumes['A'], decision.permit)
        self.assertEqual(reconcile(self.catalogue, terminal), terminal)
        self.assertEqual(permit(self.catalogue, request).terminal, terminal)
        with self.assertRaises(KeyError):
            read(self.batch.batch_id, volume=self.volumes['A'])
        self.source.close()
        self.catalogue.close()
        for name in 'BC':
            self.assertEqual(read(self.batch.batch_id, volume=self.volumes[name]), self.report)

    def test_paused_permit_and_replacement(self):
        self.prepare_rotation()
        first = permit(self.catalogue, self.request('A'))
        self.assertEqual(first.status, 'permitted')
        self.assertEqual(read(self.batch.batch_id, volume=self.volumes['A']), self.report)
        rejected = permit(self.catalogue, self.request('B', 'B-initial'))
        self.assertIsNone(rejected.permit, 'CUSTODY_FLOOR_BREACH')
        self.assertEqual(rejected.status, 'rejected')
        self.archive('C2', 'C')
        self.assertIsNone(permit(self.catalogue, self.request('B', 'B-same-domain')).permit)
        copy = self.archive('D', accept=False, report=read(self.batch.batch_id, volume=self.volumes['C']))
        self.assertIsNone(permit(self.catalogue, self.request('B', 'B-before-acceptance')).permit)
        verify(self.catalogue, self.volumes['D'], copy)
        second = permit(self.catalogue, self.request('B', 'B-after-acceptance'))
        self.assertEqual(second.status, 'permitted')
        self.catalogue.close()
        self.source.close()
        a_result = execute(self.volumes['A'], first.permit)
        b_result = execute(self.volumes['B'], second.permit)
        catalogue = self.own(open_catalogue(self.catalogue_path))
        self.assertEqual(reconcile(catalogue, a_result), a_result)
        self.assertEqual(reconcile(catalogue, b_result), b_result)
        self.assertEqual(eligible_domains(catalogue, self.batch.batch_id), {'C', 'D'})
        catalogue.close()
        for name in 'CD':
            report = read(self.batch.batch_id, volume=self.volumes[name])
            self.assertEqual([row.millilitres for row in report.rows], [7000, 11000])
            self.assertEqual([(row.source.document, row.source.line) for row in report.rows],
                             [('station-ledger', 21), ('station-ledger', 22)])
            self.assertEqual(report.total_millilitres, 18000)
            self.assertEqual(report.receipt, self.report.receipt)

    def test_lost_execution_reply(self):
        self.prepare_rotation()
        request = self.request('A')
        allowed = permit(self.catalogue, request)
        self.catalogue.close()
        self.source.close()
        execute(self.volumes['A'], allowed.permit)
        self.volumes['A'].close()
        catalogue = self.own(open_catalogue(self.catalogue_path))
        volume = self.own(open_volume(self.volume_path('A')))
        self.assertEqual(eligible_domains(catalogue, self.batch.batch_id), {'B', 'C'})
        self.assertIsNone(permit(catalogue, self.request('B')).permit, 'CUSTODY_FLOOR_BREACH')
        replay = permit(catalogue, request)
        self.assertEqual(replay, allowed)
        with self.assertRaises(ValueError):
            permit(catalogue, replace(request, copy=self.copies['B']))
        terminal = execute(volume, replay.permit)
        self.assertEqual(volume.execute('SELECT COUNT(*) FROM copy_exits').fetchone(), (1,))
        self.assertEqual(reconcile(catalogue, terminal), terminal)
        self.assertEqual(permit(catalogue, request).terminal, terminal)
        self.assertEqual(eligible_domains(catalogue, self.batch.batch_id), {'B', 'C'})
