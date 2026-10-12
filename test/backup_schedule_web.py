"""Run the backup schedule controller with intercepted privileged operations."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mail_security
ROOT = Path(__file__).resolve().parents[1]
DATA = {'supported': True, 'managed': False, 'dispatcher_installed': False,
        'timezone': 'CST', 'users': [{'user': 'soporte', 'retention': '1', 'suspended': False,
        'last_attempt': None, 'policy': {'frequency': 'daily', 'hour': 5, 'minute': 10, 'weekday': 0, 'day': 1}}]}


class ScheduleWebTests(unittest.TestCase):
    def request(self, **kwargs):
        data = kwargs.pop('settings', DATA)
        harness = mail_security.MailSecurityTests('runTest')
        with patch.object(mail_security, 'CONTROLLER', ROOT / 'web/list/backup/schedule/index.php'), \
             patch.object(mail_security, 'TEMPLATE', ROOT / 'web/templates/pages/list_backup_schedule.php'):
            return harness.request(settings=data, **kwargs)

    def post(self, **updates):
        return {'token': 'valid-token', 'backup_user': 'soporte', 'frequency': 'manual',
                'hour': '5', 'minute': '10', 'weekday': '0', 'day': '1', **updates}

    def test_owner_and_impersonation_cannot_view_global_scheduling(self):
        for options in ({'role': 'user'}, {'look': 'soporte'}):
            self.assertEqual(self.request(**options)['commands'], [])

    def test_readonly_cannot_save_and_has_no_configuration_form(self):
        result = self.request(post=self.post(), readonly=True)
        self.assertFalse(any('v-change' in command for command in result['commands']))
        self.assertNotIn('<form', result['html'])

    def test_csrf_prevents_all_commands(self):
        self.assertEqual(self.request(post=self.post(token='wrong'))['commands'], [])

    def test_valid_policy_quotes_user_and_every_calendar_argument(self):
        result = self.request(post=self.post())
        self.assertEqual(result['commands'], ["hestia v-change-sys-backup-schedule 'soporte' 'manual' '5' '10' '0' '1'"])
        self.assertEqual(result['headers'], ['Location: /list/backup/schedule/'])

    def test_invalid_input_never_saves(self):
        for update in ({'backup_user': 'soporte;id'}, {'frequency': ['manual']},
                       {'hour': '24'}, {'minute': '-1'}, {'day': '31'}, {'weekday': '7'}):
            result = self.request(post=self.post(**update))
            self.assertFalse(any('v-change' in command for command in result['commands']))

    def test_legacy_warning_and_all_schedule_options_are_visible(self):
        html = self.request()['html']
        self.assertIn('Other users keep the current frequency', html)
        for value in ('manual', 'daily', 'weekly', 'monthly'):
            self.assertIn('value="' + value + '"', html)

    def test_unsupported_legacy_schedule_does_not_offer_a_save_form(self):
        result = self.request(settings={**DATA, 'supported': False})
        self.assertNotIn('<form', result['html'])
        self.assertIn('custom or unsupported', result['html'])


if __name__ == '__main__':
    unittest.main()
