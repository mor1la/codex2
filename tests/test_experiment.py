"""Target-volume generation and the destructive boundary of the size experiment."""
import importlib.util
import random
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('pymongo'), 'PyMongo/BSON is required')
class ExperimentGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = SimpleNamespace(BackupArchive=Mock(return_value=Mock()))
        spec = importlib.util.spec_from_file_location('experiment_generator', ROOT / 'scripts/experiment.py')
        cls.generator = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'archive_s3': archive}):
            spec.loader.exec_module(cls.generator)

    def test_target_volume_stops_within_one_document_and_is_reproducible(self):
        args = SimpleNamespace(target_gib=0.00001, docs=100000, payload=64)
        batches = []
        collection = SimpleNamespace(insert_many=lambda batch: batches.extend(batch))
        count = self.generator.seed_collection(collection, random.Random(42), args)
        size = sum(len(self.generator.BSON.encode(d)) for d in batches)
        self.assertGreaterEqual(size, int(args.target_gib * 1024 ** 3))
        self.assertLess(size - int(args.target_gib * 1024 ** 3), 256)
        self.assertEqual(count, len(batches))
        repeated = []
        self.generator.seed_collection(SimpleNamespace(insert_many=repeated.extend), random.Random(42), args)
        self.assertEqual(batches, repeated)

    def test_original_document_count_mode_is_preserved(self):
        batches = []
        args = SimpleNamespace(target_gib=0, docs=517, payload=64)
        count = self.generator.seed_collection(SimpleNamespace(insert_many=batches.extend), random.Random(42), args)
        self.assertEqual(count, 517)
        self.assertEqual([d['_id'] for d in batches], list(range(517)))

    def test_reset_refuses_an_ordinary_database_or_replica_set(self):
        args = SimpleNamespace(reset_benchmark_database=True)
        for database, replica in (('production', 'drbench'), ('dr_benchmark', 'drlab')):
            client = Mock()
            client.admin.command.return_value = {'setName': replica}
            collection = SimpleNamespace(database=SimpleNamespace(name=database))
            with patch.object(self.generator, 'pbm') as pbm:
                with self.assertRaises(RuntimeError):
                    self.generator.run(client, collection, args)
                client.drop_database.assert_not_called()
                pbm.assert_not_called()

    def test_complete_generator_records_snapshot_and_initial_volume(self):
        from bson.timestamp import Timestamp

        class Cursor(list):
            def sort(self, *args):
                return self

        class Collection:
            database = SimpleNamespace(name='dr_benchmark')
            full_name = 'dr_benchmark.events'

            def __init__(self):
                self.documents = []

            def create_index(self, *args):
                pass

            def insert_many(self, batch):
                self.documents.extend(batch)

            def insert_one(self, item):
                self.documents.append(item)

            def find(self):
                return Cursor(sorted(self.documents, key=lambda d: d['_id']))

            def index_information(self):
                return {'_id_': {}, 'project_1': {}}

        with tempfile.TemporaryDirectory() as temporary:
            collection = Collection()
            client = MagicMock()
            client.admin.command.return_value = {'setName': 'drbench'}
            client.list_database_names.return_value = []
            oplog = client.local.__getitem__.return_value
            oplog.find_one.return_value = {'ts': Timestamp(1700000000, 1)}
            oplog.find.return_value.sort.return_value = [{'ts': Timestamp(1700000001, 1)}]
            archive = Mock()
            archive.sizes.side_effect = [{'full': 200}, {'meta': 10}, {}, {'chunk': 100}]
            report = Path(temporary) / 'report.json'
            args = SimpleNamespace(reset_benchmark_database=True, reset_drill_database=False,
                                   target_gib=0, docs=3, payload=64, seed=42, cycles=1,
                                   rate=5, duration=60, weights=[25, 60, 15],
                                   coverage_timeout=5, report=report)

            def mutate(coll, rng, options, first_id):
                coll.insert_one({'_id': first_id, 'project': 1, 'revision': 0})
                return first_id + 1, {'insert': 1, 'update': 0, 'delete': 0}

            with patch.object(self.generator, 'BackupArchive', return_value=archive), \
                    patch.object(self.generator, 'pbm', return_value='{"name":"snapshot"}'), \
                    patch.object(self.generator, 'mutate', side_effect=mutate), \
                    patch.object(self.generator, 'wait_coverage', return_value='2023-11-14T22:15:00'), \
                    patch.object(self.generator.time, 'sleep'), redirect_stdout(io.StringIO()) as output:
                self.generator.run(client, collection, args)
            data = json.loads(report.read_text())
            self.assertEqual(data['base']['count'], 3)
            self.assertEqual(data['settings']['docs'], 3)
            self.assertEqual(data['snapshot_bytes'], 210)
            self.assertEqual(data['cycles'][0]['expected']['count'], 4)
            self.assertEqual(json.loads(output.getvalue().splitlines()[0])['documents'], 3)
            client.drop_database.assert_called_once_with('dr_benchmark')
