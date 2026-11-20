from audit.retrieve import read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.revisions import load_revision
from custody.release import release
from tests.fixtures import ArchiveCase


class DailyEdgeTests(ArchiveCase):
    def test_zero_and_missing(self):
        report = self.seed((7000, None, 0, 11000))
        self.assertEqual([row.millilitres for row in report.rows], [7000, None, 0, 11000])
        self.assertEqual([row.source.line for row in report.rows], [21, 22, 23, 24])
        self.assertEqual(report.total_millilitres, 18000)
        rows = (ObservationRow('estuary', 1, None, SourceReference('dry-gauge', 4)),)
        revision = intake(self.source, 'dry-day', rows)
        empty = freeze(self.source, 'dry-day', revision)
        report = read(empty.batch_id, source=self.source, catalogue=self.catalogue)
        self.assertEqual(report.rows, rows)
        self.assertEqual(report.total_millilitres, 0)

    def test_exact_integer_total(self):
        self.seed((9223372036854775808, None, 9007199254740993, -3))
        self.archive('A')
        self.archive('B')
        self.assertTrue(release(self.catalogue, self.batch.batch_id))
        self.source.close()
        self.catalogue.close()
        actual = read(self.batch.batch_id, volume=self.volumes['B'])
        self.assertEqual(actual.total_millilitres, 9232379236109516798)
        self.assertEqual([row.millilitres for row in actual.rows],
                         [9223372036854775808, None, 9007199254740993, -3])
        self.assertEqual(actual.receipt, self.report.receipt)

    def test_valid_correction_keeps_old(self):
        self.seed((7000, None, 11000))
        correction = SourceReference('corrections', 5)
        next_revision = intake(self.source, self.day, previous_revision=self.revision,
                               correction=('estuary', 1, 8000, correction))
        revision = load_revision(self.source, self.day, next_revision)
        self.assertEqual((revision.previous, revision.corrected_row, revision.correction_source),
                         (self.revision, ('estuary', 1), correction))
        newer = freeze(self.source, self.day, next_revision)
        new_report = read(newer.batch_id, source=self.source, catalogue=self.catalogue)
        self.assertEqual([row.millilitres for row in new_report.rows], [8000, None, 11000])
        self.assertEqual([row.source for row in new_report.rows], [row.source for row in self.rows])
        self.assertEqual(new_report.total_millilitres, 19000)
        self.assertNotEqual(new_report.receipt.receipt_id, self.report.receipt.receipt_id)
        self.assertEqual(read(self.batch.batch_id, source=self.source, catalogue=self.catalogue), self.report)

    def test_invalid_intake_preserves_source(self):
        self.seed()
        for value, unit, source in ((False, 'mL', SourceReference('ledger', 1)),
                                    (8, 'L', SourceReference('ledger', 1)),
                                    (8, 'mL', SourceReference('', 1))):
            with self.subTest(value=value, unit=unit, source=source), self.assertRaises(ValueError):
                intake(self.source, 'another-day', (ObservationRow('estuary', 1, value, source),), unit=unit)
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM revisions').fetchone(), (1,))
