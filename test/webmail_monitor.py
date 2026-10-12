"""Exercise persistence, scheduling and alerts without live services or email."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from webmail_monitor import Monitor, DEFAULTS, CRON, transition, validate
from webmail_fpm_status import COUNTERS


def sample(now, queue=0, epoch=100, limit=0, error=None):
    metrics = {'pool': 'www', 'process manager': 'ondemand', **{key: 0 for key in COUNTERS}}
    metrics.update({'start time': epoch, 'listen queue': queue, 'max children reached': limit})
    return {'time': now, 'metrics': None if error else metrics, 'error': error}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = self.path / 'state'
        self.root.mkdir(mode=0o700)
        self.sent = []
        self.metric = sample(10000)['metrics']
        self.monitor = Monitor(self.root, self.path / 'settings.json', self.path / 'cron',
                               probe=lambda: self.metric, active=lambda: True,
                               notify=lambda *args: self.sent.append(args))
        self.config = {**DEFAULTS, 'enabled': True, 'recipient': 'ops@example.org'}

    def test_unconfigured_monitor_has_no_recipient_and_cannot_send_alerts(self):
        self.assertEqual(self.monitor.settings()['recipient'], '')
        self.monitor.sample(10000)
        self.assertFalse(self.monitor.config.exists())
        self.assertFalse(self.monitor.cron.exists())
        self.assertEqual(self.sent, [])
        with self.assertRaisesRegex(ValueError, 'Invalid alert recipient'):
            self.monitor.configure({**DEFAULTS, 'enabled': True})

    def test_existing_site_recipient_is_preserved(self):
        self.monitor.configure(self.config)
        self.assertEqual(self.monitor.settings()['recipient'], 'ops@example.org')
        self.assertNotEqual(self.monitor.settings()['recipient'], DEFAULTS['recipient'])

    def test_two_installations_keep_independent_alert_recipients(self):
        self.monitor.configure(self.config)
        other_root = self.path / 'other-state'
        other_root.mkdir(mode=0o700)
        other_sent = []
        other = Monitor(other_root, self.path / 'other-settings.json', self.path / 'other-cron',
                        probe=lambda: self.metric, active=lambda: True,
                        notify=lambda *args: other_sent.append(args))
        other.configure({**self.config, 'recipient': 'other@example.net'})
        self.metric['listen queue'] = 1
        for now in (10000, 10060, 10120):
            self.monitor.sample(now)
            other.sample(now)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(other_sent), 1)
        self.assertEqual(self.sent[0][0]['recipient'], 'ops@example.org')
        self.assertEqual(other_sent[0][0]['recipient'], 'other@example.net')

    def test_opt_in_creates_managed_cron_and_disabling_preserves_history(self):
        self.assertFalse(self.monitor.settings()['enabled'])
        self.monitor.configure(self.config)
        self.assertEqual(self.monitor.cron.read_text(), CRON)
        self.monitor.sample(10000)
        self.monitor.configure({**self.config, 'enabled': False})
        self.assertFalse(self.monitor.cron.exists())
        self.assertEqual(len(self.monitor.read(24, 10001)['history']), 1)
        self.assertEqual(self.monitor.config.stat().st_mode & 0o777, 0o600)

    def test_custom_cron_and_symlink_are_never_overwritten(self):
        self.monitor.cron.write_text('custom task\n')
        with self.assertRaises(ValueError):
            self.monitor.configure(self.config)
        self.assertEqual(self.monitor.cron.read_text(), 'custom task\n')
        self.monitor.cron.unlink()
        self.monitor.cron.symlink_to(self.path / 'other')
        with self.assertRaises(ValueError):
            self.monitor.configure(self.config)

    def test_invalid_recipient_and_thresholds_are_rejected(self):
        for update in ({'recipient': 'x@example.org\nBcc: bad@example.org'}, {'consecutive': 1},
                       {'queue_threshold': 0}, {'cooldown_minutes': 0}, {'enabled': 'yes'}):
            with self.assertRaises(ValueError):
                validate({**self.config, **update})

    def test_only_sustained_pressure_alerts_and_cooldown_limits_repeats(self):
        self.monitor.configure(self.config)
        self.metric['listen queue'] = 1
        for now in (10000, 10060):
            self.monitor.sample(now)
        self.assertEqual(self.sent, [])
        self.monitor.sample(10120)
        self.assertEqual(len(self.sent), 1)
        self.monitor.sample(10180)
        self.assertEqual(len(self.sent), 1)
        self.metric['listen queue'] = 0
        self.monitor.sample(10240)
        self.assertEqual(self.monitor.state()['streak'], 0)

    def test_counter_increases_can_detect_pressure_between_queue_snapshots(self):
        self.monitor.configure(self.config)
        self.monitor.sample(10000)
        for index, now in enumerate((10060, 10120, 10180), 1):
            self.metric['max children reached'] = index
            self.monitor.sample(now)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.monitor.state()['reason'], 'capacity_pressure')

    def test_repeat_alerts_respect_the_full_interval(self):
        self.monitor.configure(self.config)
        self.metric['listen queue'] = 1
        for now in range(10000, 13901, 60):
            self.monitor.sample(now)
        self.assertEqual(len(self.sent), 2)
        self.assertGreaterEqual(self.sent[1][1]['time'] - self.sent[0][1]['time'], 3600)

    def test_collector_gap_resets_streak(self):
        self.monitor.configure(self.config)
        self.metric['listen queue'] = 1
        self.monitor.sample(10000)
        self.monitor.sample(10300)
        self.assertEqual(self.monitor.state()['streak'], 1)
        self.assertEqual(self.sent, [])

    def test_old_limit_counter_is_not_a_new_event_and_restart_is_not_a_spike(self):
        previous, _ = transition({}, sample(10000, limit=5), self.config)
        following, due = transition(previous, sample(10060, limit=5), self.config)
        self.assertIsNone(following['reason'])
        self.assertFalse(due)
        restarted, _ = transition(following, sample(10120, epoch=200, limit=0), self.config)
        self.assertIsNone(restarted['reason'])

    def test_repeated_service_failure_alerts_without_exposing_service_output(self):
        self.monitor.configure(self.config)
        self.monitor.active = lambda: False
        for now in (10000, 10060, 10120):
            self.monitor.sample(now)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.monitor.state()['reason'], 'service_down')

    def test_probe_failure_is_recorded_not_presented_as_zero(self):
        self.monitor.configure(self.config)
        def fail():
            raise ValueError('private backend error')
        self.monitor.probe = fail
        self.monitor.sample(10000)
        latest = self.monitor.read(1, 10001)['state']['latest']
        self.assertIsNone(latest['metrics'])
        self.assertEqual(latest['error'], 'probe_failed')
        self.assertNotIn('private', json.dumps(latest))

    def test_failed_notification_does_not_advance_delivery_cooldown(self):
        self.monitor.configure(self.config)
        self.metric['listen queue'] = 1
        def fail(*args):
            raise ValueError('send failed')
        self.monitor.notify = fail
        for now in (10000, 10060, 10120):
            self.monitor.sample(now)
        self.assertTrue(self.monitor.state()['notification_error'])
        self.assertEqual(self.monitor.state()['last_alert'], 0)

    def test_retention_removes_only_old_history_files(self):
        self.monitor.configure(self.config)
        (self.root / '2020-01-01.jsonl').write_text('{}\n')
        (self.root / 'settings-backup-123.json').write_text('{}\n')
        self.monitor.sample(1791760875)
        self.assertFalse((self.root / '2020-01-01.jsonl').exists())
        self.assertTrue((self.root / 'settings-backup-123.json').exists())


if __name__ == '__main__':
    unittest.main()
