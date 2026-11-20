from audit.retrieve import read
from custody.catalogue import load_release_status
from custody.eligibility import eligible_domains
from custody.registry import list_verified_copies
from custody.release import release
from custody.verification import verify
from tests.controls import remove_last_copy_row
from tests.fixtures import ArchiveCase


class CustodyTests(ArchiveCase):
    def test_two_domains_release(self):
        self.seed()
        first = self.archive('A')
        second = self.archive('B')
        self.assertTrue(release(self.catalogue, self.batch.batch_id))
        self.assertTrue(load_release_status(self.catalogue, self.batch.batch_id))
        self.source.close()
        self.catalogue.close()
        for target in (first, second):
            report = read(self.batch.batch_id, volume=self.volumes[target.volume.domain], expected_copy=target)
            self.assertEqual(report, self.report)
            self.assertEqual(report.total_millilitres, 18000)

    def test_missing_row_rejects_copy(self):
        self.seed()
        self.archive('A')
        copy = self.archive('B', accept=False)
        remove_last_copy_row(self.volumes['B'], copy)
        with self.assertRaises(ValueError):
            verify(self.catalogue, self.volumes['B'], copy)
        self.assertNotIn(copy, list_verified_copies(self.catalogue, self.batch.batch_id))
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'A'})
        self.assertFalse(release(self.catalogue, self.batch.batch_id))

    def test_same_domain_is_one(self):
        self.seed()
        self.archive('A')
        self.archive('A2', 'A')
        self.assertEqual(eligible_domains(self.catalogue, self.batch.batch_id), {'A'})
        self.assertFalse(release(self.catalogue, self.batch.batch_id))
        self.assertFalse(load_release_status(self.catalogue, self.batch.batch_id))
