"""Execute the monitoring PHP route with intercepted privileged commands."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mail_security
ROOT = Path(__file__).resolve().parents[1]
CONFIG = {'enabled': False, 'recipient': 'sistemas@grupoademex.com',
          'queue_threshold': 1, 'consecutive': 3, 'cooldown_minutes': 60}


class MonitorWebTests(unittest.TestCase):
    def request(self, **kwargs):
        payload = kwargs.pop('settings', {'config': CONFIG, 'state': {}, 'history': [], 'now': 10000})
        harness = mail_security.MailSecurityTests('runTest')
        with patch.object(mail_security, 'CONTROLLER', ROOT / 'web/list/mail/performance/index.php'), \
             patch.object(mail_security, 'TEMPLATE', ROOT / 'web/templates/pages/list_mail_performance.php'):
            return harness.request(settings=payload, **kwargs)

    def post(self, **updates):
        return {'token': 'valid-token', 'enabled': 'yes', 'recipient': 'sistemas@grupoademex.com',
                'queue_threshold': '1', 'consecutive': '3', 'cooldown_minutes': '60', **updates}

    def test_users_and_impersonation_never_read_global_history(self):
        for options in ({'role': 'user'}, {'look': 'soporte'}):
            self.assertEqual(self.request(**options)['commands'], [])

    def test_readonly_has_no_configuration_form(self):
        result = self.request(readonly=True, post=self.post())
        self.assertFalse(any('v-change' in cmd for cmd in result['commands']))
        self.assertNotIn('name="recipient"', result['html'])

    def test_csrf_blocks_all_operations(self):
        self.assertEqual(self.request(post=self.post(token='bad'))['commands'], [])

    def test_valid_configuration_quotes_arguments_and_redirects(self):
        result = self.request(post=self.post())
        self.assertEqual(result['commands'], ["hestia v-change-sys-webmail-monitor 'yes' 'sistemas@grupoademex.com' '1' '3' '60'"])
        self.assertEqual(result['headers'], ['Location: /list/mail/performance/'])

    def test_invalid_settings_do_not_mutate(self):
        for update in ({'recipient': 'x@a.org\nBcc:x@b.org'}, {'enabled': ['yes']},
                       {'queue_threshold': '0'}, {'consecutive': '1'}, {'cooldown_minutes': 'x;id'}):
            result = self.request(post=self.post(**update))
            self.assertFalse(any('v-change' in cmd for cmd in result['commands']))

    def test_custom_sender_is_quoted_and_blank_resets_to_default(self):
        for sender in ('notifications@example.net', ''):
            result = self.request(post=self.post(sender=sender))
            self.assertEqual(result['commands'], [
                "hestia v-change-sys-webmail-monitor 'yes' 'sistemas@grupoademex.com' '1' '3' '60' '" + sender + "'"])

    def test_invalid_sender_never_reaches_configuration_command(self):
        for sender in (['ops@example.org'], 'x@a.org\nBcc:x@b.org', 'x;id'):
            result = self.request(post=self.post(sender=sender))
            self.assertFalse(any('v-change' in cmd for cmd in result['commands']))

    def test_sender_form_handles_old_configuration_without_sender(self):
        self.assertIn('name="sender"', self.request()['html'])

    def test_empty_and_failed_history_reads_do_not_claim_healthy(self):
        self.assertIn('Enable monitoring to begin collecting history.', self.request()['html'])
        result = self.request(failure=True)
        self.assertNotIn('Recent sample available.', result['html'])

    def test_json_chart_data_cannot_inject_script(self):
        history = [{'time': 10000, 'metrics': None, 'error': '</script><script>alert(1)</script>'}]
        payload = {'config': CONFIG, 'state': {}, 'history': history, 'now': 10000}
        result = self.request(settings=payload)
        self.assertNotIn('</script><script>alert(1)', result['html'])
        self.assertIn('webmail-monitor-chart', result['html'])


if __name__ == '__main__':
    unittest.main()
