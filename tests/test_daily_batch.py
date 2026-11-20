from audit.receipts import load_receipt
from audit.retrieve import read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import SourceReference
from tests.fixtures import ArchiveCase


class DailyBatchTests(ArchiveCase):
    def test_order_and_missing(self):
        report = self.seed()
        self.assertEqual([row.millilitres for row in report.rows], [7000, None, 11000])
        self.assertEqual([row.source for row in report.rows], [
            SourceReference('station-ledger', 21), SourceReference('station-ledger', 22),
            SourceReference('station-ledger', 23),
        ])
        self.assertEqual(report.total_millilitres, 18000)
        self.assertEqual(report.receipt, load_receipt(self.catalogue, self.batch.batch_id))
        self.assertEqual(freeze(self.source, self.day, self.revision), self.batch)

    def test_invalid_revision(self):
        self.seed()
        with self.assertRaises(KeyError):
            intake(self.source, self.day, previous_revision=99,
                   correction=('estuary', 1, 8000, SourceReference('corrections', 5)))
        with self.assertRaises(KeyError):
            freeze(self.source, self.day, 99)
        self.assertEqual(read(self.batch.batch_id, source=self.source, catalogue=self.catalogue), self.report)
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM revisions').fetchone(), (1,))
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM frozen_batches').fetchone(), (1,))
