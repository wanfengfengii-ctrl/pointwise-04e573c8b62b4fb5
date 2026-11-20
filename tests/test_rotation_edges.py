from dataclasses import replace

from audit.retrieve import read
from custody.catalogue import open_catalogue
from custody.eligibility import eligible_domains
from custody.release import release
from custody.verification import verify
from media.replicate import replicate
from media.volume import open_volume
from media.withdrawal import load_exit_result
from retire.execution import execute
from retire.permits import permit
from retire.reconciliation import reconcile
from tests.fixtures import ArchiveCase


class RotationEdgeTests(ArchiveCase):
    def setUp(self):
        super().setUp()
        self.seed((7000, 11000))
        for name in 'ABC':
            self.archive(name)
        self.assertTrue(release(self.catalogue, self.batch.batch_id))

    def test_stale_precheck(self):
        competing = self.own(open_catalogue(self.catalogue_path))

        def issue_competing_permit(domains):
            self.assertEqual(domains, {'A', 'C'})
            first = permit(competing, self.request('A'))
            self.assertEqual(first.status, 'permitted')
            self.assertEqual(read(self.batch.batch_id, volume=self.volumes['A']), self.report)

        second = permit(self.catalogue, self.request('B'), after_precheck=issue_competing_permit)
        self.assertIsNone(second.permit, 'CUSTODY_FLOOR_BREACH')
        self.assertEqual(second.status, 'rejected')
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'B', 'C'})

    def test_rejected_request_replay(self):
        permit(self.catalogue, self.request('A'))
        request = self.request('B')
        rejected = permit(self.catalogue, request)
        self.assertEqual(rejected.status, 'rejected')
        self.assertIsNone(rejected.permit)
        self.archive('D')
        self.assertEqual(permit(self.catalogue, request), rejected)
        with self.assertRaises(ValueError):
            execute(self.volumes['B'], rejected.permit)
        self.assertEqual(read(self.batch.batch_id, volume=self.volumes['B']), self.report)
        self.assertEqual(permit(self.catalogue, self.request('B', 'B-new')).status, 'permitted')

    def test_wrong_volume_permit(self):
        allowed = permit(self.catalogue, self.request('A'))
        with self.assertRaises(ValueError):
            execute(self.volumes['B'], allowed.permit)
        forged = replace(allowed.permit, request=replace(allowed.request, request_id='other'))
        with self.assertRaises(ValueError):
            execute(self.volumes['A'], forged)
        self.assertEqual(read(self.batch.batch_id, volume=self.volumes['A']), self.report)
        self.assertEqual(read(self.batch.batch_id, volume=self.volumes['B']), self.report)
        self.assertIsNone(load_exit_result(self.volumes['B'], allowed.request.request_id))

    def test_terminal_replay(self):
        request = self.request('A')
        allowed = permit(self.catalogue, request)
        first = execute(self.volumes['A'], allowed.permit)
        self.volumes['A'].close()
        volume = self.own(open_volume(self.volume_path('A')))
        self.assertEqual(execute(volume, allowed.permit), first)
        with self.assertRaises(ValueError):
            reconcile(self.catalogue, replace(first, signature='0' * 64))
        self.assertEqual(permit(self.catalogue, request).status, 'permitted')
        self.assertEqual(reconcile(self.catalogue, first), first)
        self.assertEqual(reconcile(self.catalogue, first), first)
        self.assertEqual(permit(self.catalogue, request).terminal, first)
        self.assertEqual(volume.execute('SELECT COUNT(*) FROM copy_exits').fetchone(), (1,))
        with self.assertRaises(ValueError):
            replicate(self.report, volume, request.copy.copy_id)
        replacement = replicate(self.report, volume, 'new-copy-A')
        verify(self.catalogue, volume, replacement)
        self.assertEqual(execute(volume, allowed.permit), first)
        self.assertEqual(read(self.batch.batch_id, volume=volume, expected_copy=replacement), self.report)
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'A', 'B', 'C'})

    def test_repeat_verification_keeps_exclusion(self):
        permit(self.catalogue, self.request('A'))
        verify(self.catalogue, self.volumes['A'], self.copies['A'])
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'B', 'C'})
        self.assertIsNone(permit(self.catalogue, self.request('B')).permit)
