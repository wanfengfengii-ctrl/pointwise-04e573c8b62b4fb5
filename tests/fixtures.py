from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from audit.retrieve import read
from batch.freeze import freeze
from batch.intake import intake
from batch.lineage import ObservationRow, SourceReference
from batch.storage import initialize_source_store, open_source_store
from custody.catalogue import initialize_catalogue, open_catalogue
from custody.registry import register_volume_domain
from custody.verification import verify
from media.replicate import replicate
from media.volume import VolumeIdentity, initialize_volume, open_volume
from retire.requests import RetireRequest


class ArchiveCase(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source_path = self.root / 'source.sqlite'
        self.catalogue_path = self.root / 'catalogue.sqlite'
        initialize_source_store(self.source_path, 'basin-stations')
        initialize_catalogue(self.catalogue_path, 'basin-archive')
        self.source = self.own(open_source_store(self.source_path))
        self.catalogue = self.own(open_catalogue(self.catalogue_path))
        self.volumes = {}
        self.copies = {}
        self.day = '2026-10-04'

    def own(self, connection):
        self.addCleanup(connection.close)
        return connection

    def seed(self, readings=(7000, None, 11000)):
        self.rows = tuple(ObservationRow('estuary', index, value, SourceReference('station-ledger', index + 20))
                          for index, value in enumerate(readings, 1))
        self.revision = intake(self.source, self.day, self.rows)
        self.batch = freeze(self.source, self.day, self.revision)
        self.report = read(self.batch.batch_id, source=self.source, catalogue=self.catalogue)
        return self.report

    def volume_path(self, name):
        return self.root / f'{name}.sqlite'

    def volume(self, name, domain=None):
        identity = VolumeIdentity(f'station-{name}', 'archive', domain or name)
        key = sha256(f'archive-regression-{name}'.encode()).digest()
        initialize_volume(self.volume_path(name), identity, 'basin-archive', key)
        volume = self.own(open_volume(self.volume_path(name)))
        register_volume_domain(self.catalogue, volume)
        self.volumes[name] = volume
        return volume

    def archive(self, name, domain=None, *, accept=True, report=None):
        volume = self.volume(name, domain)
        copy = replicate(self.report if report is None else report, volume, f'copy-{name}')
        self.copies[name] = copy
        if accept:
            verify(self.catalogue, volume, copy)
        return copy

    def request(self, name, request_id=None):
        copy = self.copies[name]
        return RetireRequest(request_id or f'retire-{name}', copy.batch_id, copy)
