import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'custom_integration'))
from bson import ObjectId, Decimal128
from mongodb_custom_query import collector_core as core
from mongodb_custom_query.config_from_endpoint import config_from_endpoint


class MongoDBConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config_from_endpoint({
            'host': 'localhost', 'database_name': 'demo',
            'use_credential_vault': False, 'username': 'reader', 'password': 'secret',
            'queries': [{'name': 'orders', 'collection': 'orders', 'pipeline': '[]',
                         'dedup_key_fields': '_id'}],
        })
        self.config['collector']['dedup']['state_file'] = self.temp.name + '/state.db'
        self.client = MagicMock()
        self.collection = self.client['demo']['orders']

    def run_documents(self, docs):
        cursor = MagicMock()
        cursor.__iter__.return_value = iter(docs)
        self.collection.aggregate.return_value = cursor
        result = core.run_query(self.client, self.config['database'],
                                self.config['collector'], self.config['queries'][0])
        cursor.close.assert_called_once()
        return result

    def test_activation_nullable_properties_have_no_defaults(self):
        schema = json.loads((Path(__file__).resolve().parents[1] /
                             'custom_integration/extension/activationSchema.json').read_text())

        def check(value):
            if isinstance(value, dict):
                if value.get('nullable') is True:
                    self.assertNotIn('default', value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(schema)

    def test_connection_options_and_ping(self):
        with patch.object(core, 'MongoClient') as factory:
            self.assertIs(core.connect_to_mongodb(self.config['database']), factory.return_value)
            factory.return_value.admin.command.assert_called_once_with('ping')
            self.assertEqual(factory.call_args.kwargs['authSource'], 'demo')
            self.assertEqual(factory.call_args.kwargs['password'], 'secret')
            factory.return_value.admin.command.side_effect = RuntimeError('unreachable')
            with self.assertRaises(RuntimeError):
                core.connect_to_mongodb(self.config['database'])
            factory.return_value.close.assert_called_once()

    def test_bson_and_existing_row_dedup(self):
        oid = ObjectId()
        docs = [{'_id': oid, 'amount': Decimal128('123.45'), 'nested': {'id': oid}}]
        first = self.run_documents(docs)
        self.assertEqual(first[0]['result.id'], str(oid))
        self.assertEqual(first[0]['result.amount'], '123.45')
        json.dumps(first)
        repeated = self.run_documents(docs)
        self.assertEqual(repeated[0]["event.type"], "summary")
        self.assertEqual(repeated[0]["dedup_skipped_count"], 1)
        docs[0]['amount'] = Decimal128('125.00')
        self.assertEqual(len(self.run_documents(docs)), 1)
        self.collection.aggregate.assert_called_with([{'$limit': 1001}], maxTimeMS=10000)

    def test_limits_empty_results_and_nested_mode(self):
        self.config['collector']['max_rows_per_query'] = 1
        result = self.run_documents([{'_id': 1}, {'_id': 2}])
        self.assertTrue(result[0]['rows_truncated'])
        self.assertEqual(result[0]['row_count'], 1)
        empty = self.run_documents([])
        self.assertEqual(empty[0]['event.type'], 'summary')
        self.config['collector']['result_mode'] = 'nested_json'
        nested = self.run_documents([{'_id': 3}])
        self.assertEqual(nested[0]['result.mode'], 'nested_json')
        self.assertEqual(json.loads(nested[0]['result.rows_json']), [{'id': 3}])

    def test_pipeline_validation_and_extended_json(self):
        for pipeline in ['{}', '[1]', '[{"$out":"copy"}]',
                         '[{"$facet":{"nested":[{"$merge":"copy"}]}}]']:
            with self.assertRaises(ValueError):
                core.validate_pipeline(pipeline)
        parsed = core.validate_pipeline('[{"$match":{"_id":{"$oid":"507f1f77bcf86cd799439011"}}}]')
        self.assertIsInstance(parsed[0]['$match']['_id'], ObjectId)

    def test_sample_queries_and_advanced_configuration(self):
        queries = json.loads((Path(__file__).resolve().parents[1] /
                              'custom_integration/queries.json').read_text())
        core.validate_config({**self.config, 'queries': queries})
        endpoint = {'host': 'localhost', 'database_name': 'demo',
                    'use_credential_vault': True, 'credentialVaultId': 'vault',
                    'username': 'reader', 'password': 'secret', 'queries_json': json.dumps(queries)}
        self.assertEqual(len(config_from_endpoint(endpoint)['queries']), 6)
        q = self.config['queries'][0]
        c = self.config['collector']
        self.assertTrue(core.should_run_query(q, c))
        core.mark_query_ran(c, q)
        self.assertFalse(core.should_run_query(q, c))


if __name__ == '__main__':
    unittest.main()
