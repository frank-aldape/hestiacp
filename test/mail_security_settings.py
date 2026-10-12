"""Exercise mail setting transactions on temporary files with stubbed services."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from mail_security_settings import Settings
from mail_security_config import render_router, VALIDITY
from unittest.mock import patch

TEMPLATE = '''CUSTOM_MACRO = retained
begin routers

site_relay:
  driver = manualroute
  route_list = * relay.example.org
begin transports
'''


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.active, self.template, self.validity = [self.root / p for p in ('exim.conf', 'template', 'validity.cf')]
        self.active.write_text('SRS_MACRO = retained\n.include ' + str(self.template) + '\n')
        self.template.write_text(render_router(TEMPLATE, 'ops@example.org'))
        self.template.chmod(0o640)
        self.validity.write_bytes(VALIDITY.read_bytes())
        self.commands = []
        self.fail_validation = False
        self.fail_reload = 0
        self.settings = Settings(self.active, self.template, self.validity,
                                 self.root / 'backups', self.run_command)

    def run_command(self, args):
        self.commands.append(args)
        if args[0] == 'systemctl' and self.fail_reload:
            self.fail_reload -= 1
            raise ValueError('Reload failed')
        if args[0] != 'systemctl' and self.fail_validation:
            raise ValueError('Validation failed')
        if args[0] == 'exim4' and '-bP' in args:
            return 'primary_hostname = srv.example.org\n'
        return 'new@example.org\n'

    def prepare_forwarding(self):
        self.template.write_text(self.template.read_text().replace('begin transports', 'aliases:\n  driver = redirect\n  unseen\nbegin transports') + 'local_spam_delivery:\n  driver = appendfile\n')
        self.settings.domains_root = self.root / 'domains'
        (self.settings.domains_root / 'example.org').mkdir(parents=True)

    def test_selected_forwarding_policy_is_transactional_and_reversible(self):
        self.prepare_forwarding()
        previous = self.template.read_bytes()
        with patch('mail_security_settings.validate_condition') as validation:
            self.settings.change('forward-spam-policy', 'example.org')
            validation.assert_called_once()
        self.assertEqual(self.settings.read()['forwarding'], {'mode': 'selected', 'domains': ['example.org']})
        self.assertEqual(self.commands[-1], ['systemctl', 'reload', 'exim4'])
        self.settings.change('forward-spam-policy', 'off')
        self.assertEqual(self.template.read_bytes(), previous)

    def test_failed_forwarding_expansion_never_changes_live_config(self):
        self.prepare_forwarding()
        previous = self.template.read_bytes()
        with patch('mail_security_settings.validate_condition', side_effect=ValueError('expression invalid')):
            with self.assertRaisesRegex(ValueError, 'expression invalid'):
                self.settings.change('forward-spam-policy', 'all')
        self.assertEqual(self.template.read_bytes(), previous)
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_forwarding_reload_failure_restores_previous_policy(self):
        self.prepare_forwarding()
        previous = self.template.read_bytes()
        self.fail_reload = 1
        with patch('mail_security_settings.validate_condition'):
            with self.assertRaisesRegex(ValueError, 'previous configuration restored'):
                self.settings.change('forward-spam-policy', 'all')
        self.assertEqual(self.template.read_bytes(), previous)

    def test_missing_local_domain_is_rejected(self):
        self.prepare_forwarding()
        with self.assertRaisesRegex(ValueError, 'existing local mail domains'):
            self.settings.change('forward-spam-policy', 'missing.org')
        self.assertFalse(self.commands)

    def test_command_passes_long_expansions_through_standard_input(self):
        expression = 'x' * 403 + '\n'
        output = Settings.command([sys.executable, '-c', 'import sys; print(sys.stdin.read(), end="")'], input_text=expression)
        self.assertEqual(output, expression)

    def test_reads_installed_values_without_invoking_services(self):
        self.assertEqual(self.settings.read(), {
            'notifications': {'state': 'configured', 'recipient': 'ops@example.org'},
            'validity': 'disabled', 'forwarding': {'mode': 'off', 'domains': []}})
        self.assertEqual(self.commands, [])

    def test_notification_change_preserves_other_configuration_and_permissions(self):
        old = self.template.read_bytes()
        wrapper = self.active.read_bytes()
        self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.active.read_bytes(), wrapper)
        self.assertEqual(self.template.read_text(), old.decode().replace('ops@example.org', 'new@example.org'))
        self.assertEqual(self.template.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.commands[-1], ['systemctl', 'reload', 'exim4'])
        backup = next((self.root / 'backups').glob('change-*'))
        self.assertEqual((backup / 'previous').read_bytes(), old)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len([cmd for cmd in self.commands if '-bt' in cmd]), 4)

    def test_existing_settings_are_idempotent_without_reloads_or_backups(self):
        self.assertFalse(self.settings.change('notifications', 'ops@example.org'))
        self.assertFalse(self.settings.change('validity-disable'))
        self.assertEqual(self.commands, [])
        self.assertFalse((self.root / 'backups').exists())

    def test_invalid_recipient_and_hostname_destination_are_rejected(self):
        old = self.template.read_bytes()
        for recipient in ('ops@example.org\n  data = x@example.org', 'root@srv.example.org'):
            with self.assertRaises(ValueError):
                self.settings.change('notifications', recipient)
        self.assertEqual(self.template.read_bytes(), old)
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_unsupported_router_cannot_be_overwritten(self):
        self.template.write_text(self.template.read_text().replace('forbid_pipe', 'forbid_pipe\n  condition = custom'))
        old = self.template.read_bytes()
        self.assertEqual(self.settings.read()['notifications']['state'], 'unsupported')
        with self.assertRaises(ValueError):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_bytes(), old)

    def test_unsupported_include_cannot_be_overwritten(self):
        self.active.write_text('.include unsupported.conf\n')
        old = self.template.read_bytes()
        with self.assertRaises(ValueError):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_bytes(), old)

    def test_validation_failure_never_modifies_live_file_or_reloads(self):
        old = self.template.read_bytes()
        self.fail_validation = True
        with self.assertRaises(ValueError):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_bytes(), old)
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_notification_reload_failure_restores_previous_file(self):
        old = self.template.read_bytes()
        self.fail_reload = 1
        with self.assertRaisesRegex(ValueError, 'previous configuration restored'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_bytes(), old)
        self.assertEqual(len([cmd for cmd in self.commands if cmd[0] == 'systemctl']), 2)

    def test_missing_validity_file_can_be_installed_without_changing_exim(self):
        self.validity.unlink()
        old = self.template.read_bytes()
        self.settings.change('validity-disable')
        self.assertEqual(self.validity.read_bytes(), VALIDITY.read_bytes())
        self.assertEqual(self.template.read_bytes(), old)
        self.assertEqual(self.commands[-1], ['systemctl', 'reload-or-restart', 'spamd'])
        self.assertEqual(self.validity.stat().st_mode & 0o777, 0o644)

    def test_custom_validity_rules_cannot_be_overwritten(self):
        self.validity.write_text('score CUSTOM_RULE 2\n')
        self.assertEqual(self.settings.read()['validity'], 'custom')
        with self.assertRaises(ValueError):
            self.settings.change('validity-disable')
        self.assertEqual(self.validity.read_text(), 'score CUSTOM_RULE 2\n')
        self.assertEqual(self.commands, [])

    def test_failed_validity_reload_removes_new_file(self):
        self.validity.unlink()
        self.fail_reload = 1
        with self.assertRaisesRegex(ValueError, 'previous configuration restored'):
            self.settings.change('validity-disable')
        self.assertFalse(self.validity.exists())

    def test_external_change_during_validation_is_not_overwritten(self):
        def change_during_validation(args):
            self.template.write_text('changed externally\n')
            return self.run_command(args)
        self.settings.run = change_during_validation
        with self.assertRaisesRegex(ValueError, 'changed during validation'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_text(), 'changed externally\n')
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_missing_router_can_be_added(self):
        self.template.write_text(TEMPLATE)
        self.assertEqual(self.settings.read()['notifications']['state'], 'missing')
        self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.settings.read()['notifications']['recipient'], 'new@example.org')

    def generated_layout(self):
        self.active.unlink()
        generated = self.root / 'config.autogenerated'
        macros = self.root / 'exim4.conf.localmacros'
        macros.write_text('SITE_MACRO = preserved\n')
        debconf = self.root / 'update-exim4.conf.conf'
        debconf.write_text("dc_use_split_config='false'\n")
        generated.write_text(macros.read_text() + self.template.read_text())
        generated.chmod(0o640)
        self.settings.exim.generated = generated
        original_run = self.run_command

        def generate(args):
            if args[0] != 'update-exim4.conf':
                return original_run(args)
            self.commands.append(args)
            inputs = Path(args[args.index('--confdir') + 1])
            output = Path(args[args.index('--output') + 1])
            # Emulate Debian's localmacros + unsplit template generation in isolation.
            text = (inputs / macros.name).read_text() + (inputs / self.template.name).read_text()
            output.write_text('# generated file\n' + '\n'.join(
                line for line in text.splitlines() if not line.lstrip().startswith('#')) + '\n')
            return ''

        self.settings.run = generate
        return generated, debconf, macros

    def test_generated_change_preserves_macros_and_does_not_create_wrapper(self):
        generated, debconf, macros = self.generated_layout()
        debconf_before, macros_before = debconf.read_bytes(), macros.read_bytes()
        self.assertEqual(self.settings.read()['notifications']['state'], 'configured')
        self.assertFalse(self.commands)
        self.settings.change('notifications', 'new@example.org')
        self.assertFalse(self.active.exists())
        self.assertIn('SITE_MACRO = preserved', generated.read_text())
        self.assertIn('data = new@example.org', generated.read_text())
        self.assertEqual(debconf.read_bytes(), debconf_before)
        self.assertEqual(macros.read_bytes(), macros_before)
        self.assertEqual(generated.stat().st_mode & 0o777, 0o640)
        backup = next((self.root / 'backups').glob('change-*'))
        self.assertIn(b'data = ops@example.org', (backup / 'active-before').read_bytes())

    def test_generated_reload_failure_restores_template_and_active(self):
        generated, _, _ = self.generated_layout()
        old_template, old_active = self.template.read_bytes(), generated.read_bytes()
        self.fail_reload = 1
        with self.assertRaisesRegex(ValueError, 'previous configuration restored'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(self.template.read_bytes(), old_template)
        self.assertEqual(generated.read_bytes(), old_active)
        self.assertFalse(self.active.exists())

    def test_generator_failure_does_not_change_live_files(self):
        generated, _, _ = self.generated_layout()
        previous = generated.read_bytes(), self.template.read_bytes()
        original_run = self.settings.run
        def fail_generator(args):
            if args[0] == 'update-exim4.conf':
                raise ValueError('generator failed')
            return original_run(args)
        self.settings.run = fail_generator
        with self.assertRaisesRegex(ValueError, 'generator failed'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_generated_candidate_parse_failure_does_not_change_live_files(self):
        generated, _, _ = self.generated_layout()
        previous = generated.read_bytes(), self.template.read_bytes()
        self.fail_validation = True
        with self.assertRaisesRegex(ValueError, 'Validation failed'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_manually_edited_generated_file_is_not_overwritten(self):
        generated, _, _ = self.generated_layout()
        generated.write_text(generated.read_text() + 'MANUAL_MACRO = custom\n')
        previous = generated.read_bytes(), self.template.read_bytes()
        with self.assertRaisesRegex(ValueError, 'differs from its current inputs'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_split_configuration_is_not_supported_or_modified(self):
        generated, debconf, _ = self.generated_layout()
        debconf.write_text("dc_use_split_config='true'\n")
        previous = generated.read_bytes(), self.template.read_bytes()
        self.assertEqual(self.settings.read()['notifications']['state'], 'unsupported')
        with self.assertRaisesRegex(ValueError, 'unsplit'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))
        self.assertFalse(self.commands)

    def test_generated_inputs_changed_during_validation_are_not_overwritten(self):
        generated, debconf, _ = self.generated_layout()
        original_run = self.settings.run
        def change_inputs(args):
            if args[0] == 'exim4' and '-C' in args:
                debconf.write_text("dc_use_split_config='false'\n# concurrent edit\n")
            return original_run(args)
        self.settings.run = change_inputs
        previous = generated.read_bytes(), self.template.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed during validation'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))
        self.assertFalse(any(cmd[0] == 'systemctl' for cmd in self.commands))

    def test_generated_forwarding_is_validated_and_can_be_disabled(self):
        self.prepare_forwarding()
        generated, _, _ = self.generated_layout()
        previous = self.template.read_bytes()
        with patch('mail_security_settings.validate_condition') as validation:
            self.settings.change('forward-spam-policy', 'all')
            validation.assert_called_once()
        self.assertIn('hestia_spam_forward_guard:', generated.read_text())
        self.assertEqual(self.settings.read()['forwarding']['mode'], 'all')
        self.settings.change('forward-spam-policy', 'off')
        self.assertEqual(self.template.read_bytes(), previous)
        self.assertNotIn('hestia_spam_forward_guard:', generated.read_text())

    def test_reload_replacing_generated_candidate_is_detected_and_recovered(self):
        generated, _, _ = self.generated_layout()
        previous = generated.read_bytes(), self.template.read_bytes()
        original_run = self.settings.run
        changed = False
        def replace_during_reload(args):
            nonlocal changed
            result = original_run(args)
            if args[0] == 'systemctl' and not changed:
                generated.write_text('OTHER_MACRO = unexpected\n')
                changed = True
            return result
        self.settings.run = replace_during_reload
        with self.assertRaisesRegex(ValueError, 'previous configuration restored'):
            self.settings.change('notifications', 'new@example.org')
        self.assertEqual(previous, (generated.read_bytes(), self.template.read_bytes()))


class CommandTests(unittest.TestCase):
    def invoke(self, args, demo=False, spam='spamd', failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            audit = root / 'bin/v-log-action'
            audit.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$HESTIA/audit"\n')
            audit.chmod(0o700)
            environment = root / 'environment.sh'
            environment.write_text('''
source() { :; }
source_conf() { :; }
check_hestia_demo_mode() { if [ "$DEMO" = yes ]; then exit 9; fi; }
check_args() { if [ "$1" -gt "$2" ]; then exit 2; fi; }
check_result() { if [ "$1" -ne 0 ]; then echo "Error: $2"; exit "$1"; fi; }
log_event() { printf '%s\\n' "$*" >> "$HESTIA/events"; }
python3() { printf '%s\\n' "$*" > "$HESTIA/invocation"; return "$FAILURE"; }
''')
            script = Path(__file__).resolve().parents[1] / 'bin/v-change-sys-mail-security'
            env = dict(os.environ, BASH_ENV=str(environment), HESTIA=str(root),
                       BIN=str(root / 'bin'), MAIL_SYSTEM='exim4', ANTISPAM_SYSTEM=spam,
                       DEMO='yes' if demo else 'no', FAILURE='1' if failure else '0',
                       E_INVALID='2', OK='0', ARGUMENTS='test')
            result = subprocess.run(['bash', str(script), *args], env=env, capture_output=True, text=True)
            return result, (root / 'invocation').exists(), (root / 'audit').exists()

    def test_forwarding_policy_cli_is_audited_and_requires_antispam(self):
        result, invoked, audited = self.invoke(['forward-spam-policy', 'example.org'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(invoked and audited)
        for args, spam in [(['forward-spam-policy', 'all'], ''),
                           (['forward-spam-policy', 'example.org;id'], 'spamd')]:
            result, invoked, audited = self.invoke(args, spam=spam)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(invoked or audited)

    def test_successful_change_is_audited(self):
        result, invoked, audited = self.invoke(['notifications', 'ops@example.org'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(invoked)
        self.assertTrue(audited)

    def test_failed_engine_does_not_log_a_success(self):
        result, invoked, audited = self.invoke(['validity-disable'], failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(invoked)
        self.assertFalse(audited)

    def test_invalid_or_extra_arguments_never_reach_engine(self):
        for args in (['notifications', 'ops@example.org;id'],
                     ['notifications', 'ops@example.org', '/tmp/path'],
                     ['validity-disable', 'extra'], ['unknown']):
            result, invoked, audited = self.invoke(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(invoked)
            self.assertFalse(audited)

    def test_demo_and_missing_spamd_never_reach_engine(self):
        for kwargs in ({'demo': True}, {'spam': ''}):
            result, invoked, audited = self.invoke(['validity-disable'], **kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(invoked)
            self.assertFalse(audited)


if __name__ == '__main__':
    unittest.main()
