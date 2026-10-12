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
        self.assertNotIn('<form method="post"', result['html'])

    def test_csrf_prevents_all_commands(self):
        self.assertEqual(self.request(post=self.post(token='wrong'))['commands'], [])

    def test_valid_policy_quotes_user_and_every_calendar_argument(self):
        result = self.request(post=self.post())
        self.assertEqual(result['commands'], ["hestia v-change-sys-backup-schedule 'soporte' 'manual' '5' '10' '0' '1'"])
        self.assertEqual(result['headers'], ['Location: /list/backup/schedule/?backup_user=soporte'])

    def test_invalid_input_never_saves(self):
        for update in ({'backup_user': 'soporte;id'}, {'frequency': ['manual']},
                       {'hour': '24'}, {'minute': '-1'}, {'day': '31'}, {'weekday': '7'}):
            result = self.request(post=self.post(**update))
            self.assertFalse(any('v-change' in command for command in result['commands']))

    def test_legacy_warning_and_all_schedule_options_are_visible(self):
        html = self.request(query={'backup_user': 'soporte'})['html']
        self.assertIn('Other users keep the current frequency', html)
        for value in ('manual', 'daily', 'weekly', 'monthly'):
            self.assertIn('value="' + value + '"', html)

    def test_initial_screen_requires_selection_and_has_no_policy_editor(self):
        result = self.request()
        self.assertIn('backup-user-selector', result['html'])
        self.assertNotIn('<form method="post"', result['html'])

    def test_only_the_selected_user_has_an_editor(self):
        admin = {**DATA['users'][0], 'user': 'admin', 'policy': {**DATA['users'][0]['policy'], 'frequency': 'manual'}}
        data = {**DATA, 'users': [admin, DATA['users'][0]]}
        result = self.request(settings=data, query={'backup_user': 'soporte'})
        self.assertEqual(result['html'].count('<form method="post"'), 1)
        self.assertIn('name="backup_user" value="soporte"', result['html'])
        self.assertNotIn('name="backup_user" value="admin"', result['html'])

    def test_unknown_or_array_selection_cannot_open_another_user_editor(self):
        for value in ('missing', ['soporte']):
            result = self.request(query={'backup_user': value})
            self.assertNotIn('<form method="post"', result['html'])
            self.assertEqual(result['error'], 'Backup user not found.')

    def test_unsupported_legacy_schedule_does_not_offer_a_save_form(self):
        result = self.request(settings={**DATA, 'supported': False})
        self.assertNotIn('<form', result['html'])
        self.assertIn('custom or unsupported', result['html'])


if __name__ == '__main__':
    unittest.main()
