"""Validate status responses without requiring PHP-FPM or live mailboxes."""
import json
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from webmail_fpm_status import parse_response, COUNTERS


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.data = {'pool': 'www', 'process manager': 'ondemand', **{key: 0 for key in COUNTERS}}

    def response(self):
        return 'Content-Type: application/json\r\n\r\n' + json.dumps(self.data)

    def test_returns_only_aggregate_metrics(self):
        self.data.update({'listen queue': 2, 'max children reached': 3,
                          'processes': [{'request uri': '/private', 'user': 'private'}]})
        result = parse_response(self.response())
        self.assertEqual(result['listen queue'], 2)
        self.assertEqual(result['max children reached'], 3)
        self.assertNotIn('processes', result)

    def test_missing_or_bad_counters_are_not_reported_as_zero(self):
        for value in (None, -1, '0', True):
            self.data['listen queue'] = value
            with self.assertRaises(ValueError):
                parse_response(self.response())

    def test_wrong_pool_or_http_error_is_rejected(self):
        self.data['pool'] = 'other'
        with self.assertRaises(ValueError):
            parse_response(self.response())
        with self.assertRaises(ValueError):
            parse_response('Status: 404 Not Found\n\n{}')

    def test_bad_json_and_missing_headers_are_rejected(self):
        for response in ('{}', 'Content-Type: text/html\n\n<html>', 'Content-Type: text/plain\n\n[]'):
            with self.assertRaises(ValueError):
                parse_response(response)


if __name__ == '__main__':
    unittest.main()
