"""Check Exim metadata summaries against isolated log fixtures."""
from datetime import datetime
import gzip
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from mail_delivery_audit import analyze


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 11, 22)

    def test_auth_local_bounces_and_forwarded_events_are_distinguished(self):
        (self.root / 'mainlog').write_text('''2026-10-11 20:00:00 id <= user@example.org P=esmtpsa A=dovecot_login:user@example.org S=30
2026-10-11 20:00:01 id => recipient@example.net R=dnslookup T=remote_smtp H=mx.example.net
2026-10-11 20:00:02 other <= forged@attacker.org P=esmtp S=40
2026-10-11 20:00:03 other => recipient@example.net R=dnslookup T=remote_forwarded_smtp H=mx.example.net
2026-10-11 20:00:04 bounce <= <> P=local S=90
2026-10-11 20:00:05 bounce ** recipient@example.net: 550-5.7.26 sender unauthenticated DKIM did not pass
2026-10-11 20:00:06 defer == recipient@example.net: 421 4.7.0 rate limit low reputation
2026-10-11 20:00:07 app <= notifications@example.org P=local S=30
''')
        report = analyze(self.root, now=self.now)
        self.assertEqual(report['totals']['accepted_messages'], 4)
        self.assertEqual(report['totals']['authenticated_submissions'], 1)
        self.assertEqual(report['top_authenticated_sender_domains'], {'example.org': 1})
        self.assertEqual(report['totals']['remote_delivery_events'], 2)
        self.assertEqual(report['totals']['forwarded_delivery_events'], 1)
        self.assertEqual(report['failure_smtp_codes'], {'550 5.7.26': 1, '421 4.7.0': 1})
        self.assertEqual(report['failure_reason_hints']['authentication'], 1)
        self.assertEqual(report['failure_reason_hints']['rate_limit'], 1)
        self.assertEqual(report['top_local_sender_domains_excluding_bounces'], {'example.org': 1})
        self.assertEqual(report['local_messages_by_day_excluding_bounces'], {'2026-10-11': 1})
        self.assertNotIn('user@example.org', str(report))
        self.assertNotIn('recipient@example.net', str(report))

    def test_rotated_gzip_window_and_invalid_lines(self):
        with gzip.open(self.root / 'mainlog.1.gz', 'wt') as stream:
            stream.write('2026-10-10 12:00:00 id <= user@example.org P=esmtpa A=login:user@example.org\n')
            stream.write('2026-09-01 12:00:00 old <= older@example.org P=esmtpa A=login:older@example.org\n')
            stream.write('2026-10-12 12:00:00 future <= later@example.org P=local\n')
            stream.write('not a log entry\n')
        report = analyze(self.root, now=self.now)
        self.assertEqual(report['totals']['accepted_messages'], 1)
        self.assertEqual(report['available_period']['first'], '2026-10-10T12:00:00')
        self.assertEqual(report['authenticated_messages_by_day'], {'2026-10-10': 1})

    def test_corrupt_archive_is_reported_without_claiming_complete_data(self):
        (self.root / 'mainlog.1.gz').write_bytes(b'not gzip')
        report = analyze(self.root, now=self.now)
        self.assertEqual(report['unreadable_or_incomplete_logs'], ['mainlog.1.gz'])
        self.assertIsNone(report['available_period']['first'])


if __name__ == '__main__':
    unittest.main()
