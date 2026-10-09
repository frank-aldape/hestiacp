"""Run with python3 test/mail_security.py (PHP CLI required)."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "web/list/mail/security/index.php"
TEMPLATE = ROOT / "web/templates/pages/list_mail_security.php"


class MailSecurityTests(unittest.TestCase):
    def request(self, post=None, role="admin", readonly=False, look="", service="exim4",
                hosts=None, failure=False, settings=None):
        source = CONTROLLER.read_text().removeprefix("<?php")
        source = source.replace('include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";', "")
        source = source.replace("exit();", "throw new \\MailSecurityRouteExit();")
        fixture = {
            "post": post or {}, "role": role, "readonly": readonly, "look": look,
            "service": service, "hosts": hosts if hosts is not None else ["dnsbl.example.net"],
            "failure": failure, "template": str(TEMPLATE),
            "settings": settings if settings is not None else {
                "notifications": {"state": "configured", "recipient": "ops@example.org"},
                "validity": "disabled",
                "forwarding": {"mode": "off", "domains": []},
            },
        }
        script = r'''<?php
namespace {
    class MailSecurityRouteExit extends \RuntimeException {}
    function tohtml($text) { return htmlspecialchars($text, ENT_QUOTES, "UTF-8"); }
    function show_alert_message($session) {}
    if (!function_exists("_")) { function _($text) { return $text; } }
}
namespace Hestiacp\quoteshellarg {
    function quoteshellarg($text) { return escapeshellarg($text); }
}
namespace MailSecurityTest {
    function header($value) { $GLOBALS["result"]["headers"][] = $value; }
    function verify_csrf($post) {
        $GLOBALS["result"]["csrf"] = true;
        if (($post["token"] ?? "") !== $_SESSION["token"]) {
            throw new \MailSecurityRouteExit();
        }
    }
    function exec($command, &$output, &$status) {
        $GLOBALS["result"]["commands"][] = $command;
        $status = $GLOBALS["fixture"]["failure"] ? 1 : 0;
        $output = str_contains($command, "v-list-sys-mail-security")
            ? [json_encode($GLOBALS["fixture"]["settings"])]
            : (str_contains($command, "v-list-") ? $GLOBALS["fixture"]["hosts"] : []);
        return "";
    }
    function check_return_code($status, $output) {
        if ($status !== 0) { $_SESSION["error_msg"] = "CLI failure"; }
    }
    function render_page($user, $tab, $page) {
        $GLOBALS["result"]["error"] = $_SESSION["error_msg"] ?? null;
        $GLOBALS["result"]["loaded"] = $GLOBALS["v_dnsbl_loaded"];
        extract($GLOBALS);
        ob_start();
        include $fixture["template"];
        $GLOBALS["result"]["html"] = ob_get_clean();
    }
    define("HESTIA_CMD", "hestia ");
    $fixture = json_decode(base64_decode("FIXTURE"), true);
    $_SESSION = ["userContext" => $fixture["role"], "look" => $fixture["look"],
        "MAIL_SYSTEM" => $fixture["service"], "ANTISPAM_SYSTEM" => "spamd",
        "ANTIVIRUS_SYSTEM" => "", "token" => "valid-token"];
    $_SERVER["REQUEST_METHOD"] = empty($fixture["post"]) ? "GET" : "POST";
    $_POST = $fixture["post"];
    $read_only = $fixture["readonly"];
    $user = "admin";
    $result = ["commands" => [], "headers" => [], "csrf" => false];
    try { eval(base64_decode("CONTROLLER")); } catch (\MailSecurityRouteExit $e) {}
    echo json_encode($result);
}
'''
        import base64
        script = script.replace("FIXTURE", base64.b64encode(json.dumps(fixture).encode()).decode())
        script = script.replace("CONTROLLER", base64.b64encode(
            ("namespace MailSecurityTest;\n" + source).encode()).decode())
        command = shlex.split(os.environ.get("PHP_COMMAND", "php"))
        completed = subprocess.run(command + ["-r", script.removeprefix("<?php")],
                                   cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(completed.stderr, "", completed.stderr)
        return json.loads(completed.stdout)

    def test_forwarding_scope_submission_is_quoted_and_redirected(self):
        for scope, domains, policy in [('off', '', 'off'), ('all', '', 'all'),
                                       ('selected', 'Example.org,other.org', 'example.org,other.org')]:
            result = self.request(post={'token': 'valid-token', 'action': 'forward-spam-policy',
                                        'forward_scope': scope, 'forward_domains': domains})
            self.assertEqual(result['commands'], ["hestia v-change-sys-mail-security forward-spam-policy '" + policy + "'"])
            self.assertEqual(result['headers'], ['Location: /list/mail/security/'])

    def test_invalid_forwarding_scope_never_mutates(self):
        for scope, domains in [('selected', ''), ('selected', 'all'), ('selected', 'off'), ('selected', 'a.org;id'),
                               ('selected', ['a.org']), (['all'], ''), ('unknown', ''),
                               ('selected', 'a.org\nother.org')]:
            result = self.request(post={'token': 'valid-token', 'action': 'forward-spam-policy',
                                        'forward_scope': scope, 'forward_domains': domains})
            self.assertFalse(any('v-change-' in cmd for cmd in result['commands']))
            self.assertEqual(result['error'], 'Invalid forwarding protection policy.')

    def test_forwarding_write_requires_csrf_and_writable_admin(self):
        for options in ({'role': 'user'}, {'look': 'customer'}, {'readonly': True}):
            result = self.request(post={'token': 'valid-token', 'action': 'forward-spam-policy',
                                        'forward_scope': 'all'}, **options)
            self.assertFalse(any('v-change-' in cmd for cmd in result['commands']))
        result = self.request(post={'token': 'wrong', 'action': 'forward-spam-policy', 'forward_scope': 'all'})
        self.assertEqual(result['commands'], [])

    def test_users_and_impersonated_sessions_cannot_read_global_settings(self):
        for kwargs in ({"role": "user"}, {"look": "customer"}):
            with self.subTest(kwargs=kwargs):
                result = self.request(**kwargs)
                self.assertEqual(result["commands"], [])
                self.assertEqual(result["headers"], ["Location: /list/mail/"])

    def test_readonly_cannot_write_and_has_no_forms_or_editor_links(self):
        result = self.request(post={"token": "valid-token", "action": "delete", "host": "dnsbl.example.net"}, readonly=True)
        self.assertTrue(result["csrf"])
        self.assertEqual(result["commands"], ["hestia v-list-sys-mail-security json", "hestia v-list-sys-mail-dnsbl plain"])
        self.assertNotIn("<form", result["html"])
        self.assertNotIn("/edit/server/", result["html"])

    def test_csrf_failure_prevents_all_commands(self):
        result = self.request(post={"token": "wrong", "action": "add", "host": "dnsbl.example.net"})
        self.assertTrue(result["csrf"])
        self.assertEqual(result["commands"], [])

    def test_invalid_payloads_never_run_a_mutation(self):
        for host, action in [("zone;touch /tmp/x", "add"), ("zone\nother", "delete"),
                             (["zone"], "add"), ("", "add"), ("zone", ["add"]),
                             ("zone", "restart"), ("zone!=127.0.0.1;id", "add")]:
            with self.subTest(host=host, action=action):
                result = self.request(post={"token": "valid-token", "action": action, "host": host})
                self.assertEqual(result["commands"], ["hestia v-list-sys-mail-security json", "hestia v-list-sys-mail-dnsbl plain"])
                self.assertEqual(result["error"], "Invalid DNSBL entry.")

    def test_existing_response_exclusions_are_preserved_and_post_redirects(self):
        for action in ("add", "delete"):
            result = self.request(post={"token": "valid-token", "action": action, "host": " zone.example!=127.0.0.1,127.0.0.2 "})
            verb = "add" if action == "add" else "delete"
            self.assertEqual(result["commands"], [f"hestia v-{verb}-sys-mail-dnsbl 'zone.example!=127.0.0.1,127.0.0.2' no"])
            self.assertEqual(result["headers"], ["Location: /list/mail/security/"])

    def test_failed_read_is_not_presented_as_an_empty_success(self):
        result = self.request(failure=True)
        self.assertFalse(result["loaded"])
        self.assertNotIn("No DNSBL entries are configured.", result["html"])
        self.assertNotIn('name="host"', result["html"])

    def test_failed_write_does_not_show_a_success_redirect(self):
        result = self.request(post={"token": "valid-token", "action": "add", "host": "zone.example"}, failure=True)
        self.assertEqual(result["headers"], [])
        self.assertEqual(result["error"], "CLI failure")

    def test_unsupported_mail_service_cannot_mutate_dnsbl(self):
        result = self.request(post={"token": "valid-token", "action": "add", "host": "zone.example"}, service="remote")
        self.assertEqual(result["commands"], ["hestia v-list-sys-mail-security json"])
        self.assertEqual(result["error"], "DNSBL management requires Exim.")

    def test_cached_entries_are_escaped_in_html(self):
        result = self.request(hosts=["", "# comment", '<script>alert("x")</script>'])
        self.assertNotIn("<script>", result["html"])
        self.assertIn("&lt;script&gt;", result["html"])
        self.assertNotIn("# comment", result["html"])

    def test_notification_destination_is_saved_using_quoted_cli_argument(self):
        result = self.request(post={"token": "valid-token", "action": "notifications",
                                    "notification_email": "ops+server@example.org"})
        self.assertEqual(result["commands"], ["hestia v-change-sys-mail-security notifications 'ops+server@example.org'"])
        self.assertEqual(result["headers"], ["Location: /list/mail/security/"])

    def test_invalid_notification_parameters_never_mutate(self):
        for email in (["ops@example.org"], "ops@example.org\nX: injected", "$(id)@example.org", ""):
            result = self.request(post={"token": "valid-token", "action": "notifications",
                                        "notification_email": email})
            self.assertFalse(any('v-change-' in cmd for cmd in result["commands"]))
            self.assertEqual(result["error"], "Invalid notification email or unsupported mail service.")

    def test_new_actions_require_csrf_and_write_access(self):
        for action in ("notifications", "validity-disable"):
            post = {"token": "wrong", "action": action, "notification_email": "ops@example.org"}
            self.assertEqual(self.request(post=post)["commands"], [])
            post["token"] = "valid-token"
            result = self.request(post=post, readonly=True)
            self.assertFalse(any('v-change-' in cmd for cmd in result["commands"]))
            self.assertNotIn('<form', result["html"])

    def test_validity_correction_has_a_separate_action(self):
        result = self.request(post={"token": "valid-token", "action": "validity-disable"})
        self.assertEqual(result["commands"], ["hestia v-change-sys-mail-security validity-disable"])

    def test_custom_settings_are_readonly_and_cached_recipient_is_escaped(self):
        result = self.request(settings={"notifications": {"state": "unsupported", "recipient": ""}, "validity": "custom"})
        self.assertNotIn('name="notification_email"', result["html"])
        self.assertNotIn('name="action" value="validity-disable"', result["html"])
        result = self.request(settings={"notifications": {"state": "configured", "recipient": '<script>alert("x")</script>'}, "validity": "disabled"})
        self.assertNotIn('<script>', result["html"])
        self.assertIn('&lt;script&gt;', result["html"])

    def test_missing_validity_file_has_apply_button(self):
        result = self.request(settings={"notifications": {"state": "missing", "recipient": ""}, "validity": "missing"})
        self.assertIn('name="action" value="validity-disable"', result["html"])

    def test_malformed_settings_do_not_show_mutation_forms(self):
        result = self.request(settings={"notifications": [], "validity": "disabled"})
        self.assertNotIn('name="notification_email"', result["html"])
        self.assertNotIn('name="action" value="validity-disable"', result["html"])
        self.assertEqual(result["error"], "Unable to read mail security settings.")


class DnsblCommandTests(unittest.TestCase):
    def command(self, verb, host, initial, reload_failure=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for directory in ("conf", "bin", "install/exim"):
                (root / directory).mkdir(parents=True)
            config = root / "conf/dnsbl.conf"
            config.write_text(initial)
            (root / "install/exim/dnsbl.conf").write_text("stock.example\n")
            (root / "bin/v-log-action").write_text("#!/bin/bash\nexit 0\n")
            (root / "bin/v-restart-service").write_text(
                '#!/bin/bash\nprintf "%s\\n" "$*" > "$HESTIA/reload.txt"\n'
                + ("exit 5\n" if reload_failure else "exit 0\n"))
            for executable in (root / "bin").iterdir():
                executable.chmod(0o700)
            # Stub only the external Hestia environment and redirect /etc writes.
            # The repository's add/delete commands themselves run unmodified.
            environment = root / "environment.sh"
            environment.write_text(r'''
source() { :; }
source_conf() { :; }
check_args() { :; }
check_hestia_demo_mode() { :; }
log_event() { :; }
check_result() {
    if [ "$1" -ne 0 ]; then printf '%s\n' "$2" >&2; exit "$1"; fi
}
cp() {
    local args=("$@")
    local last=$((${#args[@]} - 1))
    if [ "${args[$last]}" = /etc/exim4/dnsbl.conf ]; then
        args[$last]="$HESTIA/active-dnsbl.conf"
    fi
    command cp "${args[@]}"
}
''')
            if sys.platform == "darwin":
                with environment.open("a") as stream:
                    stream.write('sed() { if [ "$1" = -i ]; then shift; command sed -i "" "$@"; else command sed "$@"; fi; }\n')
            env = dict(os.environ, BASH_ENV=str(environment), HESTIA=str(root),
                       BIN=str(root / "bin"), HESTIA_INSTALL_DIR=str(root / "install"),
                       MAIL_SYSTEM="exim4", E_INVALID="2", E_EXISTS="3", E_NOTEXIST="4", E_RESTART="5")
            result = subprocess.run(["bash", str(ROOT / f"bin/v-{verb}-sys-mail-dnsbl"), host, "no"],
                                    env=env, capture_output=True, text=True)
            return result, config.read_text() if config.exists() else None

    def test_delete_removes_only_the_literal_entry(self):
        result, content = self.command("delete", "zone.example", "zone.example\nzoneXexample\nother.example\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(content, "zoneXexample\nother.example\n")

    def test_similar_entry_is_not_a_duplicate(self):
        result, content = self.command("add", "zone.example", "zoneXexample\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(content, "zoneXexample\nzone.example\n")

    def test_similar_entry_cannot_be_deleted_as_a_match(self):
        result, content = self.command("delete", "zone.example", "zoneXexample\n")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(content, "zoneXexample\n")

    def test_literal_duplicate_is_rejected(self):
        result, content = self.command("add", "zone.example", "zone.example\n")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(content, "zone.example\n")

    def test_reload_failure_is_reported_after_persisting(self):
        result, content = self.command("add", "zone.example", "other.example\n", reload_failure=True)
        self.assertEqual(result.returncode, 5)
        self.assertIn("list saved, but mail service reload failed", result.stderr)
        self.assertIn("zone.example\n", content)


if __name__ == "__main__":
    unittest.main()
