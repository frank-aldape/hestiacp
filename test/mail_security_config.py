"""Run with python3 test/mail_security_config.py; no live mail services required."""
import importlib.util
from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mail_security_config', ROOT / 'install/common/mail_security_config.py')
config = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(config)

TEMPLATE = '''CUSTOM_MACRO = site-specific
begin routers

custom_relay:
  driver = manualroute
  route_list = * relay.example.org

dnslookup:
  driver = dnslookup
  transport = remote_forwarded_smtp
begin transports
remote_forwarded_smtp:
  driver = smtp
'''


class ConfigurationTests(unittest.TestCase):
    def test_only_system_addresses_are_redirected(self):
        result = config.render_router(TEMPLATE, 'ops@example.org')
        self.assertIn('domains = $primary_hostname', result)
        self.assertIn('local_parts = root : postmaster : mailer-daemon', result)
        self.assertIn('data = ops@example.org', result)
        self.assertIn('forbid_file', result)
        self.assertIn('forbid_pipe', result)
        self.assertIn(TEMPLATE.split('begin routers')[1].strip(), result)
        self.assertLess(result.index('hestia_system_notifications:'), result.index('custom_relay:'))

    def test_repeat_preparation_does_not_duplicate_router(self):
        first = config.render_router(TEMPLATE, 'ops@example.org')
        self.assertEqual(config.render_router(first, 'ops@example.org'), first)
        changed = config.render_router(first, 'other@example.org')
        self.assertEqual(changed.count('hestia_system_notifications:'), 1)
        self.assertNotIn('data = ops@example.org', changed)
        self.assertIn('data = other@example.org', changed)

    def test_custom_existing_router_is_not_overwritten(self):
        existing = config.render_router(TEMPLATE, 'ops@example.org').replace(
            'forbid_pipe', 'forbid_pipe\n  condition = custom-expression')
        with self.assertRaises(ValueError):
            config.render_router(existing, 'other@example.org')

    def test_invalid_parameters_cannot_inject_exim_configuration(self):
        for address in ('ops@example.org\n  data = evil@example.org', '|id',
                        'ops@example.org #comment', 'root', 'ops@-example.org',
                        'ops@example..org'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                config.render_router(TEMPLATE, address)

    def test_missing_or_duplicate_sections_fail_closed(self):
        for template in ('no routers', TEMPLATE + '\nbegin routers\n'):
            with self.assertRaises(ValueError):
                config.render_router(template, 'ops@example.org')

    def test_wrapper_macros_are_preserved(self):
        source = 'SRS_KEY = site-specific\n.include /etc/exim4/exim4.conf.template\n'
        result = config.redirect_include(source, Path('/etc/exim4/exim4.conf.template'), Path('/root/stage/template-candidata'))
        self.assertEqual(result, 'SRS_KEY = site-specific\n.include /root/stage/template-candidata\n')
        with self.assertRaises(ValueError):
            config.redirect_include(source + source, Path('/etc/exim4/exim4.conf.template'), Path('/root/stage/template-candidata'))

    def test_preparation_backs_up_without_modifying_live_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            active, template, antispam = [base / p for p in ('exim.conf', 'template.conf', 'validity.cf')]
            active.write_text('SRS_KEY = private-test-value\n.include ' + str(template) + '\n')
            template.write_text(TEMPLATE)
            antispam.write_text('score CUSTOM_RULE 1\n')
            originals = {p: p.read_bytes() for p in (active, template, antispam)}
            output = config.prepare(active, template, 'ops@example.org', base / 'stage', antispam)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for path, contents in originals.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertEqual((output / 'config-anterior').read_bytes(), originals[active])
            self.assertEqual((output / 'template-anterior').read_bytes(), originals[template])
            self.assertEqual((output / 'validity-anterior').read_bytes(), originals[antispam])
            self.assertIn(str(output / 'template-candidata'), (output / 'config-candidata').read_text())
            with self.assertRaises(ValueError):
                config.prepare(active, template, 'ops@example.org', output, antispam)

    def test_validity_correction_matches_all_six_upstream_scores(self):
        scores = [line.split() for line in config.VALIDITY.read_text().splitlines() if line.startswith('score ')]
        self.assertEqual(len(scores), 6)
        for directive, name, value in scores:
            self.assertEqual(directive, 'score')
            self.assertTrue(name.startswith('RCVD_IN_VALIDITY_'))
            self.assertEqual(value, '0')
        self.assertNotIn('required_score', config.VALIDITY.read_text())


if __name__ == '__main__':
    unittest.main()
