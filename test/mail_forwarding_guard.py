"""Check selective policy rendering, preservation and fail-closed validation."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from mail_forwarding_guard import policy_domains, render_forwarding_guard, forwarding_state, validate_condition, ASSET, expansion_result

TEMPLATE = '''begin routers
site_relay:
  driver = manualroute
  route_list = * custom.example.org
autoreplay:
  driver = accept
aliases:
  driver = redirect
  unseen
  data = custom-SRS-data
catchall:
  driver = redirect
begin transports
local_spam_delivery:
  driver = appendfile
'''


class ForwardingTests(unittest.TestCase):
    def test_roundtrip_and_selection_preserve_all_site_options(self):
        selected = render_forwarding_guard(TEMPLATE, 'B.example.org,a.example.org,B.example.org')
        self.assertEqual(forwarding_state(selected), {'mode': 'selected', 'domains': ['a.example.org', 'b.example.org']})
        self.assertEqual(render_forwarding_guard(selected, 'off'), TEMPLATE)
        self.assertLess(selected.index('autoreplay:'), selected.index('hestia_spam_forward_guard:'))
        self.assertLess(selected.index('hestia_spam_forward_guard:'), selected.index('aliases:'))
        self.assertEqual(forwarding_state(render_forwarding_guard(selected, 'all'))['mode'], 'all')
        self.assertEqual(render_forwarding_guard(selected, 'a.example.org,b.example.org'), selected)

    def test_injected_or_invalid_policy_is_rejected(self):
        for value in ('', 'a.org\n', 'a.org:other.org', '*.org', '-bad.org', 'a.org;id', 'a.org,,b.org', 'a'*64 + '.org', 'a.org,'*900):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy_domains(value)

    def test_custom_or_incomplete_guard_cannot_be_overwritten(self):
        original = render_forwarding_guard(TEMPLATE, 'all')
        for changed in (original.replace('driver = accept', 'driver = redirect'),
                        original.replace('# END HESTIA SPAM FORWARDING GUARD', '# removed'),
                        original + original, TEMPLATE + 'hestia_spam_forward_guard:\n'):
            with self.assertRaises(ValueError):
                render_forwarding_guard(changed, 'off')

    def test_guard_requires_mailbox_antispam_and_internal_score(self):
        asset = ASSET.read_text()
        self.assertIn('$acl_m2', asset)
        self.assertIn('/antispam', asset)
        self.assertIn('/passwd', asset)
        self.assertNotIn('$h_', asset)
        self.assertNotIn('devnull', asset)
        for changed in (TEMPLATE.replace('aliases:', 'other:'), TEMPLATE.replace('local_spam_delivery:', 'other:')):
            with self.assertRaises(ValueError):
                render_forwarding_guard(changed, 'all')

    def test_expansion_fixture_checks_all_boundary_and_mailbox_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            results = iter(['50', 'no', 'no', 'no', 'yes', 'no', 'no'])
            def engine(args, input_text=None):
                if input_text is not None:
                    self.assertEqual(args[-1], '-be')
                    self.assertTrue(input_text.endswith('\n'))
                    self.assertGreater(len(input_text), 256)
                calls.append(args + ([input_text.strip()] if input_text is not None else []))
                if len(calls) == 7:
                    self.assertFalse((Path(directory) / 'guard-fixture/fixture.example.org/antispam').exists())
                return next(results)
            validate_condition(engine, Path(directory) / 'wrapper', Path(directory))
            self.assertEqual(len(calls), 7)
            self.assertIn('{missing}', calls[5][-1])
            self.assertTrue(all('$acl_m2' not in call[-1] for call in calls))
            self.assertTrue(all('/etc/exim4/domains/' not in call[-1] for call in calls))

    def test_interactive_expansion_prompts_are_parsed_without_hiding_errors(self):
        self.assertEqual(expansion_result('> no\n> '), 'no')
        self.assertEqual(expansion_result('yes\n'), 'yes')
        for output in ('> Failed to expand string: syntax error\n> ', '> yes\nno\n>', ''):
            with self.assertRaises(ValueError):
                expansion_result(output)

    def test_expansion_validation_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def bad_engine(args, input_text=None):
                calls.append(args)
                return '50' if args[-1] == 'SPAM_SCORE' else 'yes'
            with self.assertRaisesRegex(ValueError, 'expansion check failed'):
                validate_condition(bad_engine, Path(directory) / 'wrapper', Path(directory))
            self.assertEqual(len(calls), 2)
            self.assertTrue(all('-be' in call for call in calls))


if __name__ == '__main__':
    unittest.main()
