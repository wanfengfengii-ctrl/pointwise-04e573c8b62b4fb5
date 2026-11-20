"""Regression for late corrections of already released V1 batches."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from audit.receipts import load_receipt
from audit.retrieve import read
from batch.lineage import SourceReference
from correction_cases import CORRECTION, ROWS, StopAfter, seed_day
from custody.catalogue import open_catalogue
from custody.corrections import publish_correction, successor_batch_id
from custody.verification import verify
from media.readback import PendingCorrectionError
from media.volume import CopyIdentity, open_volume, volume_settings


class CorrectionCase(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.day = seed_day(self.root)

    def open_connections(self, *names):
        connections = []
        for name in names:
            connection = open_volume(self.root / f'{name}.sqlite')
            self.addCleanup(connection.close)
            connections.append(connection)
        catalogue = open_catalogue(self.root / 'catalogue.sqlite')
        self.addCleanup(catalogue.close)
        return catalogue, connections

    def publish(self, catalogue, c, d, *, request_id='late-1', correction=CORRECTION,
                targets=None, parent=None, after_event=None):
        if targets is None:
            targets = ((c, 'new-C'), (d, 'new-D'))
        if parent is None:
            parent = self.day.copies['C']
        return publish_correction(catalogue, request_id, c, parent, correction, targets,
                                  after_event=after_event)


class HappyPathTests(CorrectionCase):
    def test_two_volumes_independently_receive_new_ledger(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, receipt = self.publish(catalogue, c, d)
        self.assertEqual(batch_id, successor_batch_id(
            'basin-archive', self.day.old.batch_id, 'late-1'))
        reports = {}
        for name, volume in (('C', c), ('D', d)):
            report = read(batch_id, volume=volume)
            reports[name] = report
            self.assertEqual([row.millilitres for row in report.rows], [8000, None, 11000])
            self.assertEqual([row.source for row in report.rows],
                             [row.source for row in ROWS])
            self.assertEqual(report.total_millilitres, 19000)
            self.assertEqual(report.request_id, 'late-1')
            self.assertEqual(report.receipt, receipt)
            self.assertEqual(report.parent_receipt, self.day.old.receipt)
            self.assertEqual(report.parent_copy, self.day.copies['C'])
            self.assertEqual(report.before, ROWS[0])
            self.assertEqual(report.correction_source, SourceReference('late-note', 5))
        self.assertEqual(reports['C'].receipt, reports['D'].receipt)
        self.assertEqual(load_receipt(catalogue, batch_id), receipt)

    def test_old_ledger_still_reads_from_both_volumes(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, _ = self.publish(catalogue, c, d)
        for volume in (c, d):
            old = read(self.day.old.batch_id, volume=volume)
            self.assertEqual(old, self.day.old)
            self.assertEqual(old.total_millilitres, 18000)
        self.assertNotEqual(batch_id, self.day.old.batch_id)

    def test_expected_copy_binding(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, _ = self.publish(catalogue, c, d)
        target = CopyIdentity(volume_settings(c).identity, batch_id, 'new-C')
        self.assertEqual(read(batch_id, volume=c, expected_copy=target).batch_id, batch_id)
        with self.assertRaises(ValueError):
            read(batch_id, volume=c, expected_copy=replace(target, copy_id='other'))
        with self.assertRaises(ValueError):
            read(batch_id, volume=d, expected_copy=target)

    def test_missing_to_measured_zero_keeps_total_and_position(self):
        zero = ('estuary', 2, 0, SourceReference('late-note', 5))
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, receipt = self.publish(catalogue, c, d, request_id='zero-1',
                                         correction=zero)
        report = read(batch_id, volume=c)
        self.assertEqual([row.millilitres for row in report.rows], [7000, 0, 11000])
        self.assertEqual(report.total_millilitres, 18000)
        self.assertEqual(report.before.millilitres, None)
        self.assertEqual(report.before.source, ROWS[1].source)
        self.assertEqual([row.source for row in report.rows],
                         [row.source for row in ROWS])
        self.assertEqual(read(batch_id, volume=d).receipt, receipt)

    def test_equal_value_change_keeps_correction_source(self):
        same = ('estuary', 1, 7000, SourceReference('late-note', 9))
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, _ = self.publish(catalogue, c, d, request_id='equal-1', correction=same)
        report = read(batch_id, volume=c)
        self.assertEqual([row.millilitres for row in report.rows], [7000, None, 11000])
        self.assertEqual(report.total_millilitres, 18000)
        self.assertEqual(report.correction_source, SourceReference('late-note', 9))
        self.assertEqual(report.before, ROWS[0])


class InterruptionTests(CorrectionCase):
    def _interrupt(self, event, site=None):
        catalogue, (c, d) = self.open_connections('C', 'D')
        stop = StopAfter(event, site)
        with self.assertRaises(InterruptedError):
            self.publish(catalogue, c, d, after_event=stop)
        return catalogue, c, d, stop

    def test_stop_after_prepared(self):
        catalogue, c, d, stop = self._interrupt('prepared', 'station-C')
        batch_id = stop.batch_id
        with self.assertRaises(KeyError):
            load_receipt(catalogue, batch_id)
        with self.assertRaises(PendingCorrectionError):
            read(batch_id, volume=c)
        with self.assertRaises(KeyError):
            read(batch_id, volume=d)
        for volume in (c, d):
            self.assertEqual(read(self.day.old.batch_id, volume=volume), self.day.old)
        # reopen with the same binding completes and keeps the same successor id
        completed_id, receipt = self.publish(catalogue, c, d)
        self.assertEqual(completed_id, batch_id)
        self.assertEqual(read(batch_id, volume=c).receipt, receipt)
        self.assertEqual(read(batch_id, volume=d).receipt, receipt)

    def test_stop_after_published(self):
        catalogue, c, d, stop = self._interrupt('published')
        batch_id = stop.batch_id
        self.assertEqual(load_receipt(catalogue, batch_id).batch_id, batch_id)
        for volume in (c, d):
            with self.assertRaises(PendingCorrectionError):
                read(batch_id, volume=volume)
        completed_id, receipt = self.publish(catalogue, c, d)
        self.assertEqual(completed_id, batch_id)
        self.assertEqual(read(batch_id, volume=c).receipt, receipt)
        self.assertEqual(read(batch_id, volume=d).receipt, receipt)

    def test_stop_after_first_available(self):
        catalogue, c, d, stop = self._interrupt('available', 'station-C')
        batch_id = stop.batch_id
        self.assertEqual(read(batch_id, volume=c).total_millilitres, 19000)
        with self.assertRaises(PendingCorrectionError):
            read(batch_id, volume=d)
        # C keeps serving the new ledger fully offline without the catalogue
        offline = read(batch_id, volume=c)
        self.assertEqual(offline.request_id, 'late-1')
        self.assertEqual(offline.parent_receipt, self.day.old.receipt)

    def test_stop_after_second_available_then_replay(self):
        catalogue, c, d, stop = self._interrupt('available', 'station-D')
        batch_id = stop.batch_id
        first_id, first_receipt = self.publish(catalogue, c, d)
        self.assertEqual(first_id, batch_id)
        second_id, second_receipt = self.publish(catalogue, c, d)
        self.assertEqual((second_id, second_receipt), (first_id, first_receipt))

    def test_resume_skips_committed_stages_without_callback(self):
        events = []
        catalogue, (c, d) = self.open_connections('C', 'D')
        stop = StopAfter('prepared', 'station-C')
        with self.assertRaises(InterruptedError):
            self.publish(catalogue, c, d, after_event=stop)

        def record(event, batch_id, target):
            events.append((event, target.volume.site if target else None))

        self.publish(catalogue, c, d, after_event=record)
        # C's prepare was already committed, so only D's prepare, publication
        # and the two availability deliveries are observed.
        self.assertEqual(events, [
            ('prepared', 'station-D'),
            ('published', None),
            ('available', 'station-C'),
            ('available', 'station-D'),
        ])


class RejectionTests(CorrectionCase):
    def test_same_domain_targets_rejected(self):
        nested = TemporaryDirectory()
        self.addCleanup(nested.cleanup)
        day = seed_day(Path(nested.name), add_same_domain=True)
        catalogue = open_catalogue(Path(nested.name) / 'catalogue.sqlite')
        self.addCleanup(catalogue.close)
        c = open_volume(Path(nested.name) / 'C.sqlite')
        c2 = open_volume(Path(nested.name) / 'C2.sqlite')
        self.addCleanup(c.close)
        self.addCleanup(c2.close)
        with self.assertRaises(ValueError):
            publish_correction(catalogue, 'sd', c, day.copies['C'], CORRECTION,
                               ((c, 'a'), (c2, 'b')))
        self.assertEqual(catalogue.execute(
            'SELECT COUNT(*) FROM correction_requests').fetchone(), (0,))

    def test_wrong_parent_copy_rejected(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        wrong = replace(self.day.copies['C'], copy_id='missing-copy')
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d, parent=wrong)

    def test_parent_on_wrong_volume_rejected(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        with self.assertRaises(ValueError):
            publish_correction(catalogue, 'wrong-volume', d, self.day.copies['C'],
                               CORRECTION, ((c, 'a'), (d, 'b')))

    def test_missing_parent_row_is_key_error(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        missing = ('estuary', 99, 1, SourceReference('late-note', 5))
        with self.assertRaises(KeyError):
            self.publish(catalogue, c, d, correction=missing)

    def test_bad_inputs_rejected(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        cases = (
            (' ', CORRECTION, ((c, 'a'), (d, 'b'))),
            ('late-1', ('estuary', 1, 8.5, SourceReference('late-note', 5)),
             ((c, 'a'), (d, 'b'))),
            ('late-1', ('estuary', 0, 8000, SourceReference('late-note', 5)),
             ((c, 'a'), (d, 'b'))),
            ('late-1', CORRECTION, ((c, 'a'),)),
        )
        for request_id, correction, targets in cases:
            with self.subTest(request_id=request_id):
                with self.assertRaises(ValueError):
                    publish_correction(catalogue, request_id, c, self.day.copies['C'],
                                       correction, targets)

    def test_different_params_reopen_rejected(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        batch_id, receipt = self.publish(catalogue, c, d)
        alternative = ('estuary', 1, 9999, SourceReference('late-note', 5))
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d, correction=alternative)
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d, targets=((c, 'new-C'), (d, 'other')))
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d,
                         parent=self.day.copies['D'])
        # the fixed binding still serves the original material
        self.assertEqual(read(batch_id, volume=c).receipt, receipt)

    def test_tampered_candidate_blocks_publication(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        stop = StopAfter('prepared', 'station-C')
        with self.assertRaises(InterruptedError):
            self.publish(catalogue, c, d, after_event=stop)
        batch_id = stop.batch_id
        c.execute('UPDATE correction_candidate_rows SET reading=? WHERE copy_id=? AND position=0',
                  ('12345', 'new-C'))
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d)
        with self.assertRaises(KeyError):
            load_receipt(catalogue, batch_id)
        # the tampered station material fails its own seal real-read
        with self.assertRaises(ValueError):
            read(batch_id, volume=c)

    def test_failed_validation_persists_nothing(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        with self.assertRaises(ValueError):
            self.publish(catalogue, c, d,
                         targets=((c, 'a'), (c, 'b')))
        self.assertEqual(catalogue.execute(
            'SELECT COUNT(*) FROM correction_requests').fetchone(), (0,))
        for volume in (c, d):
            self.assertEqual(volume.execute(
                'SELECT COUNT(*) FROM correction_candidates').fetchone(), (0,))

    def test_candidate_write_failure_rolls_back_and_reopens(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        c.execute('''CREATE TRIGGER reject_candidate BEFORE INSERT ON correction_candidate_rows
            BEGIN SELECT RAISE(ABORT, 'candidate write rejected'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.publish(catalogue, c, d, request_id='rollback-1')
        self.assertEqual(c.execute(
            'SELECT COUNT(*) FROM correction_candidates').fetchone(), (0,))
        c.execute('DROP TRIGGER reject_candidate')
        batch_id, receipt = self.publish(catalogue, c, d, request_id='rollback-1')
        self.assertEqual(read(batch_id, volume=c).receipt, receipt)
        self.assertEqual(read(batch_id, volume=d).receipt, receipt)


class LargeIntegerTests(TestCase):
    def test_correction_keeps_cross_boundary_integers_exact(self):
        from batch.freeze import freeze as freeze_batch
        from batch.intake import intake as intake_rows
        from batch.lineage import ObservationRow, SourceReference
        from batch.storage import initialize_source_store, open_source_store
        from custody.catalogue import initialize_catalogue
        from custody.registry import register_volume_domain
        from custody.release import release
        from media.replicate import replicate
        from media.volume import (
            VolumeIdentity, initialize_volume, locate_batch_copy,
        )
        from secrets import token_bytes

        huge = 9223372036854775808
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, catalogue_path = root / 'source.sqlite', root / 'catalogue.sqlite'
            initialize_source_store(source_path, 'big-source')
            initialize_catalogue(catalogue_path, 'big-catalogue')
            with closing(open_source_store(source_path)) as source, \
                    closing(open_catalogue(catalogue_path)) as catalogue:
                rows = (
                    ObservationRow('estuary', 1, huge, SourceReference('ledger', 1)),
                    ObservationRow('estuary', 2, None, SourceReference('ledger', 2)),
                )
                revision = intake_rows(source, 'big-day', rows)
                batch = freeze_batch(source, 'big-day', revision)
                report = read(batch.batch_id, source=source, catalogue=catalogue)
                volumes = {}
                for name, domain in (('X', 'X'), ('Y', 'Y')):
                    path = root / f'{name}.sqlite'
                    initialize_volume(path, VolumeIdentity(f'station-{name}', 'archive', domain),
                                      'big-catalogue', token_bytes(32))
                    volume = open_volume(path)
                    self.addCleanup(volume.close)
                    register_volume_domain(catalogue, volume)
                    verify(catalogue, volume, replicate(report, volume, f'old-{name}'))
                    volumes[name] = volume
                self.assertTrue(release(catalogue, batch.batch_id))
                parent = locate_batch_copy(volumes['X'], batch.batch_id)
                correction = ('estuary', 1, huge + 1, SourceReference('late-note', 7))
                batch_id, receipt = publish_correction(
                    catalogue, 'big-late', volumes['X'], parent, correction,
                    ((volumes['X'], 'new-X'), (volumes['Y'], 'new-Y')))
                for volume in volumes.values():
                    result = read(batch_id, volume=volume)
                    self.assertEqual(result.total_millilitres, huge + 1)
                    self.assertEqual([row.millilitres for row in result.rows],
                                     [huge + 1, None])
                    self.assertEqual(result.receipt, receipt)


class ReadbackGateTests(CorrectionCase):
    def test_unknown_batch_is_key_error(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        with self.assertRaises(KeyError):
            read('correction-' + '0' * 64, volume=c)

    def test_pending_then_published_gate(self):
        catalogue, (c, d) = self.open_connections('C', 'D')
        stop = StopAfter('prepared', 'station-D')
        with self.assertRaises(InterruptedError):
            self.publish(catalogue, c, d, after_event=stop)
        batch_id = stop.batch_id
        for volume in (c, d):
            with self.assertRaises(PendingCorrectionError):
                read(batch_id, volume=volume)
        self.assertTrue(issubclass(PendingCorrectionError, ValueError))
        batch_id_2, receipt = self.publish(catalogue, c, d)
        self.assertEqual(batch_id_2, batch_id)
        for volume in (c, d):
            self.assertEqual(read(batch_id, volume=volume).receipt, receipt)
