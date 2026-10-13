"""Scan gate scope, custom ACL refusal and Exim expansion boundaries."""
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'install/common'))
from mail_scan_limit import render_scan_limit, validate_scan_limit


class ScanLimitTests(unittest.TestCase):
    def test_real_hestia_templates_change_only_the_size_gate(self):
        for path in (ROOT / 'install/deb/exim').glob('exim4.conf*template'):
            original = path.read_text()
            updated = render_scan_limit(original, '5')
            self.assertEqual(updated.replace('{5M}', '{1024K}'), original)
            self.assertEqual(render_scan_limit(updated, '5'), updated)

    def test_invalid_values_never_produce_a_candidate(self):
        template = (ROOT / 'install/deb/exim/exim4.conf.template').read_text()
        for value in ('0', '26', '-1', '5M', '5;id', '01', 5):
            with self.assertRaises(ValueError):
                render_scan_limit(template, value)

    def test_custom_or_ambiguous_acl_is_refused(self):
        template = (ROOT / 'install/deb/exim/exim4.conf.template').read_text()
        for modified in (template.replace('{1024K}', '{CUSTOM_LIMIT}'),
                         template.replace('debian-spamd:true/defer_ok', 'custom-spamd'),
                         template.replace('acl_check_data:', 'custom_data:'),
                         template.replace('condition      = ${if < {$message_size}{1024K}}',
                                          'condition      = ${if < {$message_size}{1024K}}\n'
                                          '          condition      = ${if < {$message_size}{1024K}}')):
            with self.assertRaises(ValueError):
                render_scan_limit(modified, '5')

    def test_expansion_failures_stop_validation(self):
        calls = []
        def run(args, input_text):
            calls.append(input_text)
            return '> ' + ('yes' if len(calls) == 1 else 'no') + '\n> '
        validate_scan_limit(run, Path('/private/candidate'), '5')
        self.assertIn('5242879', calls[0])
        self.assertIn('5242880', calls[1])
        self.assertIn('5242881', calls[2])
        with self.assertRaises(ValueError):
            validate_scan_limit(lambda *args, **kwargs: 'yes', Path('/private/candidate'), '5')


if __name__ == '__main__':
    unittest.main()
