"""Regression tests for mail listing; run with python3 test/mail_list_json.py."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PHP = shlex.split(os.environ.get('PHP_COMMAND', 'php'))


def encoded(value):
    if isinstance(value, str):
        value = value.encode()
    return base64.b64encode(value).decode()


class MailListTests(unittest.TestCase):
    def render(self, data, kind='domains', missing=False):
        # Create the fixture inside PHP's filesystem, including when using PHP WASM.
        script = '''
$directory = '/tmp';
if (!is_dir($directory)) { mkdir($directory, 0700, true); }
$file = $directory . '/mail-list-' . bin2hex(random_bytes(8));
file_put_contents($file, base64_decode('%s'));
if (%s) { unlink($file); }
$argv = ['list', '%s', $file, ' PATH IFS HESTIA USER_DATA BASH_ENV ', ' BACKUP '];
register_shutdown_function(function () use ($file) { if (is_file($file)) unlink($file); });
include base64_decode('%s');
''' % (encoded(data), 'true' if missing else 'false', kind,
       encoded(str(ROOT / 'func/list_mail_json.php')))
        return subprocess.run(PHP + ['-r', script], cwd=ROOT,
                              capture_output=True, text=True)

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def test_domains_preserve_fields_and_string_values(self):
        data = self.success(self.render("DOMAIN='example.org' ANTIVIRUS='yes' ANTISPAM='no' REJECT='yes' RATE_LIMIT='200' DKIM='yes' CATCHALL='' ACCOUNTS='9' U_DISK='104' SSL='yes' SUSPENDED='no' TIME='12:00:00' DATE='2026-10-08' WEBMAIL_ALIAS='correo' WEBMAIL='roundcube'\n"))
        self.assertEqual(data['example.org'], dict(ANTIVIRUS='yes', ANTISPAM='no',
            REJECT='yes', RATE_LIMIT='200', DKIM='yes', CATCHALL='', ACCOUNTS='9',
            U_DISK='104', SSL='yes', SUSPENDED='no', TIME='12:00:00',
            DATE='2026-10-08', WEBMAIL_ALIAS='correo', WEBMAIL='roundcube'))

    def test_accounts_keep_numeric_names_as_object_keys(self):
        result = self.render("ACCOUNT='123' ALIAS='ventas,soporte' FWD='a@example.org,b@example.org' FWD_ONLY='no' AUTOREPLY='yes' QUOTA='0' U_DISK='10' SUSPENDED='no' TIME='12:00:00' DATE='2026-10-08'", 'accounts')
        data = self.success(result)
        self.assertIsInstance(data, dict)
        self.assertEqual(data['123']['QUOTA'], '0')
        self.assertEqual(data['123']['ALIAS'], 'ventas,soporte')
        self.assertEqual(len(data['123']), 9)

    def test_empty_and_blank_lines_return_object(self):
        self.assertEqual(self.success(self.render('\n  \n\r\n')), {})

    def test_missing_fields_do_not_leak_between_records(self):
        data = self.success(self.render("DOMAIN='a.org' DKIM='yes'\nDOMAIN='b.org'\n"))
        self.assertEqual(data['a.org']['DKIM'], 'yes')
        self.assertEqual(data['b.org']['DKIM'], '')

    def test_quotes_unicode_backslashes_and_shell_expressions_are_data(self):
        value = 'áé "texto" $(id); `whoami` \\ruta'
        data = self.success(self.render("DOMAIN='a.org' CATCHALL='" + value + "'\n"))
        self.assertEqual(data['a.org']['CATCHALL'], value)
        escaped = self.success(self.render("DOMAIN=a.org CATCHALL=ab\\ c'de f'ghi\n"))
        self.assertEqual(escaped['a.org']['CATCHALL'], 'ab cde fghi')

    def test_invalid_records_fail_without_partial_json(self):
        for record in ("DOMAIN='broken", 'eval(code)=123', "DOMAIN='a.org' BAD=value\\", "ANTISPAM='yes'", "DOMAIN=''"):
            with self.subTest(record=record):
                result = self.render("DOMAIN='valid.org'\n" + record + '\n')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr)

    def test_missing_file_and_invalid_mode_fail(self):
        for result in (self.render('', missing=True), self.render('', kind='invalid')):
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')

    def test_141_domains_and_1300_accounts(self):
        domains = ''.join("DOMAIN='d%d.org' ACCOUNTS='10'\n" % i for i in range(141))
        accounts = ''.join("ACCOUNT='user%d' QUOTA='1024'\n" % i for i in range(1300))
        self.assertEqual(len(self.success(self.render(domains))), 141)
        self.assertEqual(len(self.success(self.render(accounts, 'accounts'))), 1300)

    def test_shared_parser_reserved_keys_duplicates_and_literal_values(self):
        records = [
            ["KEY='abc'def EMPTY='' ESC=ab\\ cd QUOTE=\\'", {'KEY': 'abcdef', 'EMPTY': '', 'ESC': 'ab cd', 'QUOTE': "'"}],
            ["PATH='evil' IFS='x' BASH_FUNC_evil='x' HESTIA='bad' BACKUP='allowed' DOMAIN='a.org'", {'BACKUP': 'allowed', 'DOMAIN': 'a.org'}],
            ["KEY='first' KEY='last'", {'KEY': 'last'}],
            ["UNICODE='áø中' ESC=\\ø", {'UNICODE': 'áø中', 'ESC': 'ø'}],
        ]
        script = '''require base64_decode('%s');
$tests=json_decode(base64_decode('%s'),true);
$results=[];
foreach ($tests as $test) $results[]=parse_hestia_object($test[0], ['PATH','IFS','HESTIA'], ['BACKUP']);
echo json_encode($results);''' % (encoded(str(ROOT / 'func/object_parser.php')), encoded(json.dumps(records)))
        result = subprocess.run(PHP + ['-r', script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [r[1] for r in records])
        self.assertIn('Duplicate key name', result.stderr)

    def test_shell_entrypoints_invoke_php_once_and_propagate_failure(self):
        for name, kind, suffix in [('v-list-mail-domains', 'domains', 'mail.conf'),
                                   ('v-list-mail-accounts', 'accounts', 'mail/example.org.conf')]:
            source = (ROOT / 'bin' / name).read_text()
            function = source[source.index('json_list() {'):source.index('# SHELL list function')]
            with tempfile.TemporaryDirectory() as directory:
                launcher = Path(directory) / 'php'
                calls = Path(directory) / 'calls'
                launcher.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\nprintf "{}\\n"\nexit "${FAILURE:-0}"\n')
                launcher.chmod(0o700)
                env = dict(os.environ, HESTIA=str(ROOT), USER_DATA=directory,
                           HESTIA_PHP=str(launcher), CALLS=str(calls), E_INVALID='2',
                           domain='example.org')
                script = 'check_result() { if [ "$1" != 0 ]; then exit "$3"; fi; }\n' + function + '\njson_list\n'
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '{}\n')
                self.assertEqual(len(calls.read_text().splitlines()), 1)

                self.assertIn(kind + ' ' + directory + '/' + suffix, calls.read_text())
                calls.unlink()
                env['FAILURE'] = '2'
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertEqual(len(calls.read_text().splitlines()), 1)

    def test_shell_parser_still_quotes_values_and_rejects_invalid_input(self):
        source = (ROOT / 'func/main.sh').read_text()
        function = source[source.index('_parse_object_kv_list_php() {'):source.index('\nparse_object_kv_list() {')]
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / 'php'
            launcher.write_text('#!/bin/bash\nexec ' + shlex.join(PHP) + ' "$@"\n')
            launcher.chmod(0o700)
            env = dict(os.environ, HESTIA=str(ROOT), HESTIA_PHP=str(launcher),
                       HESTIA_RESERVED_CONF_KEYS=' PATH HESTIA USER_DATA ',
                       HESTIA_OBJECT_KEY_EXCEPTIONS=' BACKUP ', E_INVALID='2')
            script = 'check_result() { if [ "$1" != 0 ]; then exit "$3"; fi; }\n' + function
            script += '\n_parse_object_kv_list_php "$1"\nprintf "%s\\n" "$KEY" "$EMPTY" "$HESTIA"\n'
            value = "$(id); `whoami` 'quoted' \\path"
            record = "KEY='" + value.replace("'", "'\\''") + "' EMPTY='' HESTIA='evil'"
            result = subprocess.run(['bash', '-c', script, 'test', record], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, value + '\n\n' + str(ROOT) + '\n')
            result = subprocess.run(['bash', '-c', script, 'test', "KEY='broken"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')



if __name__ == '__main__':
    unittest.main()
