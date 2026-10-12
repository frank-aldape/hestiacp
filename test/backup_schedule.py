"""Exercise scheduling migration, preservation and queue idempotency on fixtures."""
import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from backup_schedule import Schedules, MANUAL, CRON, validate, due, legacy_schedule

LEGACY = 'MAILTO=root\n41 4 * * * sudo /usr/local/hestia/bin/v-update-sys-hestia-all\n10 5 * * * sudo /usr/local/hestia/bin/v-backup-users\n*/2 * * * * custom-task\n'


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.users = self.root / 'users'
        for name in ('admin', 'soporte'):
            path = self.users / name
            path.mkdir(parents=True)
            (path / 'user.conf').write_text("BACKUPS='1'\nSUSPENDED='no'\n")
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.crontab = LEGACY
        self.calls = []
        self.fail_crontab = False
        self.model = Schedules(self.users, self.root / 'settings.json', self.state, self.root / 'cron', self.run_command)

    def run_command(self, args, input_text=None):
        self.calls.append((args, input_text))
        if args[:3] == ['crontab', '-u', 'hestiaweb']:
            if args[-1] == '-l':
                return subprocess.CompletedProcess(args, 0, self.crontab, '')
            if self.fail_crontab:
                return subprocess.CompletedProcess(args, 1, '', 'rejected')
            self.crontab = input_text
        return subprocess.CompletedProcess(args, 0, '', '')

    def test_initial_read_does_not_change_daily_job_or_create_settings(self):
        data = self.model.read()
        self.assertFalse(data['managed'])
        self.assertEqual(data['users'][1]['policy']['frequency'], 'daily')
        self.assertEqual(self.crontab, LEGACY)
        self.assertFalse(self.model.config.exists())

    def test_manual_migration_preserves_other_jobs_and_other_user_frequency(self):
        self.model.configure('soporte', MANUAL)
        self.assertEqual(self.crontab, LEGACY.replace('10 5 * * * sudo /usr/local/hestia/bin/v-backup-users\n', ''))
        self.assertEqual(self.model.cron.read_text(), CRON)
        users = self.model.read()['users']
        self.assertEqual(users[0]['policy']['frequency'], 'daily')
        self.assertEqual(users[1]['policy']['frequency'], 'manual')
        self.assertEqual(self.model.config.stat().st_mode & 0o777, 0o600)

    def test_custom_and_duplicate_backup_cron_are_refused_without_changes(self):
        for text in (LEGACY + '0 0 * * * sudo /usr/local/hestia/bin/v-backup-users\n',
                     LEGACY.replace('10 5', '*/5 *'), LEGACY.replace('v-backup-users\n', 'v-backup-users > /tmp/log\n')):
            self.crontab = text
            with self.assertRaises(ValueError):
                self.model.configure('soporte', MANUAL)
            self.assertEqual(self.crontab, text)
            self.assertFalse(self.model.config.exists())

    def test_failed_crontab_migration_rolls_back_settings_and_dispatcher(self):
        self.fail_crontab = True
        with self.assertRaises(ValueError):
            self.model.configure('soporte', MANUAL)
        self.assertEqual(self.crontab, LEGACY)
        self.assertFalse(self.model.config.exists())
        self.assertFalse(self.model.cron.exists())

    def test_nonexistent_and_injected_user_or_calendar_is_refused(self):
        for user in ('missing', '../admin', 'soporte;id'):
            with self.assertRaises(ValueError):
                self.model.configure(user, MANUAL)
        for update in ({'frequency': 'hourly'}, {'hour': 24}, {'day': 31}, {'weekday': True}):
            with self.assertRaises(ValueError):
                validate({**MANUAL, **update})

    def test_queue_dispatch_is_per_user_and_idempotent(self):
        self.model.configure('soporte', {**MANUAL, 'frequency': 'weekly', 'weekday': 0})
        now = datetime.datetime(2026, 10, 11, 5, 10)
        self.model.dispatch(now)
        self.model.dispatch(now)
        queued = [args[-1] for args, _ in self.calls if args[0].endswith('v-schedule-user-backup')]
        self.assertEqual(queued, ['admin', 'soporte'])
        self.assertEqual(self.model.state()['soporte']['status'], 'queued')

    def test_manual_suspended_and_disabled_users_are_not_enqueued(self):
        self.model.configure('soporte', MANUAL)
        (self.users / 'admin/user.conf').write_text("BACKUPS='1'\nSUSPENDED='yes'\n")
        self.model.dispatch(datetime.datetime(2026, 10, 11, 5, 10))
        self.assertFalse(any(args[0].endswith('v-schedule-user-backup') for args, _ in self.calls))

    def test_busy_and_failed_queue_results_are_not_reported_as_completed_backups(self):
        self.model.configure('soporte', {**MANUAL, 'frequency': 'daily'})
        old_run = self.model.run
        def queue_result(args, input_text=None):
            if args[0].endswith('v-schedule-user-backup'):
                return subprocess.CompletedProcess(args, 4 if args[-1] == 'admin' else 2, '', '')
            return old_run(args, input_text)
        self.model.run = queue_result
        self.model.dispatch(datetime.datetime(2026, 10, 11, 5, 10))
        self.assertEqual(self.model.state()['admin']['status'], 'already_scheduled')
        self.assertEqual(self.model.state()['soporte']['status'], 'failed')
        self.assertEqual(self.model.state()['soporte']['exit_code'], 2)

    def test_custom_timezone_is_not_silently_moved_to_system_time(self):
        self.crontab = 'CRON_TZ=UTC\n' + LEGACY
        with self.assertRaisesRegex(ValueError, 'timezone'):
            self.model.configure('soporte', MANUAL)
        self.assertFalse(self.model.config.exists())

    def test_legacy_weekly_and_monthly_frequency_and_timezone_calendar(self):
        _, weekly = legacy_schedule('10 5 * * 7 ' + 'sudo /usr/local/hestia/bin/v-backup-users\n')
        self.assertTrue(due(weekly, datetime.datetime(2026, 10, 11, 5, 10)))
        self.assertFalse(due(weekly, datetime.datetime(2026, 10, 12, 5, 10)))
        _, monthly = legacy_schedule('10 5 20 * * sudo /usr/local/hestia/bin/v-backup-users\n')
        self.assertTrue(due(monthly, datetime.datetime(2026, 10, 20, 5, 10)))
        self.assertFalse(due(monthly, datetime.datetime(2026, 10, 21, 5, 10)))

    def test_crontab_concurrent_edit_is_not_overwritten(self):
        calls = 0
        original = self.model.legacy_cron
        def changing():
            nonlocal calls
            calls += 1
            text = original()
            return text + '# concurrent\n' if calls >= 3 else text
        self.model.legacy_cron = changing
        with self.assertRaisesRegex(ValueError, 'changed during preparation'):
            self.model.configure('soporte', MANUAL)
        self.assertFalse(self.model.config.exists())


if __name__ == '__main__':
    unittest.main()
