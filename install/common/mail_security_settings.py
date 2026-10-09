#!/usr/bin/env python3
"""Read and update the supported site mail settings through the privileged CLI."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from mail_security_config import render_router, redirect_include, VALIDITY
from mail_forwarding_guard import forwarding_state, policy_domains, render_forwarding_guard, validate_condition


class Settings:
    def __init__(self, active=Path('/etc/exim4/exim4.conf'),
                 template=Path('/etc/exim4/exim4.conf.template'),
                 validity=Path('/etc/mail/spamassassin/zz-validity-disabled.cf'),
                 backups=Path('/root/hestia-mail-security'), run=None,
                 domains_root=Path('/etc/exim4/domains')):
        self.active, self.template, self.validity, self.backups = map(
            Path, (active, template, validity, backups))
        self.run = run or self.command
        self.domains_root = Path(domains_root)

    @staticmethod
    def command(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode:
            # Do not expose service output, which may contain configuration secrets.
            raise ValueError('Mail configuration validation or service reload failed')
        return result.stdout

    def notification_state(self):
        # Verify the supported include layout even when no router exists yet.
        redirect_include(self.active.read_text(), self.template, self.template)
        text = self.template.read_text()
        if not re.search(r'^hestia_system_notifications:', text, re.M):
            render_router(text, 'test@example.org')
            return {'state': 'missing', 'recipient': ''}
        recipients = re.findall(
            r'^hestia_system_notifications:[ \t]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n)*',
            text, re.M)
        if len(recipients) != 1:
            raise ValueError('Unsupported notification router')
        recipient = re.search(r'^\s+data = ([^\n]+)$', recipients[0], re.M)
        if not recipient:
            raise ValueError('Unsupported notification destination')
        email = recipient[1].strip()
        if render_router(text, email) != text:
            raise ValueError('Unsupported notification router')
        return {'state': 'configured', 'recipient': email}

    def validity_state(self):
        if not self.validity.exists():
            return 'missing'
        # Ignore comments/whitespace, but refuse to overwrite other site directives.
        lines = [line.split('#', 1)[0].strip() for line in self.validity.read_text().splitlines()]
        lines = [line for line in lines if line]
        expected = [line for line in VALIDITY.read_text().splitlines() if line.startswith('score ')]
        if sorted(lines) == sorted(expected):
            return 'disabled'
        return 'custom'

    def read(self):
        try:
            notifications = self.notification_state()
        except (OSError, ValueError):
            notifications = {'state': 'unsupported', 'recipient': ''}
        try:
            validity = self.validity_state()
        except OSError:
            validity = 'unsupported'
        try:
            redirect_include(self.active.read_text(), self.template, self.template)
            forwarding = forwarding_state(self.template.read_text())
        except (OSError, ValueError):
            forwarding = {'mode': 'unsupported', 'domains': []}
        return {'notifications': notifications, 'validity': validity, 'forwarding': forwarding}

    @staticmethod
    def replace(path, content, metadata=None):
        # Live paths are fixed by this module, never supplied by the web request.
        descriptor, temporary = tempfile.mkstemp(prefix='.hestia-security-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), metadata.st_mode & 0o777 if metadata else 0o644)
                if metadata:
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def change(self, action, recipient=''):
        if action == 'notifications':
            self.notification_state()  # Refuse independently customized routers/layouts.
            path = self.template
            candidate = render_router(path.read_text(), recipient).encode()
            service = ['systemctl', 'reload', 'exim4']
        elif action == 'validity-disable':
            if self.validity_state() not in ('missing', 'disabled'):
                raise ValueError('Validity override contains custom rules; use the service editor')
            path = self.validity
            candidate = VALIDITY.read_bytes()
            service = ['systemctl', 'reload-or-restart', 'spamd']
        elif action == 'forward-spam-policy':
            mode, domains = policy_domains(recipient)
            if any(not (self.domains_root / domain).is_dir() for domain in domains):
                raise ValueError('Forwarding protection requires existing local mail domains')
            path = self.template
            candidate = render_forwarding_guard(path.read_text(), recipient).encode()
            service = ['systemctl', 'reload', 'exim4']
        else:
            raise ValueError('Unknown mail security action')
        if path.is_symlink():
            raise ValueError('Symlinked configuration requires manual review')
        previous = path.read_bytes() if path.exists() else None
        if previous == candidate:
            return False
        metadata = path.stat() if previous is not None else None
        self.backups.mkdir(mode=0o700, parents=False, exist_ok=True)
        self.backups.chmod(0o700)
        stage = Path(tempfile.mkdtemp(prefix='change-', dir=self.backups))
        if previous is not None:
            shutil.copy2(path, stage / 'previous')
        (stage / 'candidate').write_bytes(candidate)
        (stage / 'manifest.json').write_text(json.dumps({
            'action': action, 'path': str(path), 'previous_exists': previous is not None,
        }, indent=2) + '\n')
        exim_change = action in ('notifications', 'forward-spam-policy')
        active_before = self.active.read_bytes() if exim_change else None
        if exim_change:
            shutil.copy2(self.active, stage / 'active-before')
            wrapper = redirect_include(active_before.decode(), self.template, stage / 'candidate')
            (stage / 'config-candidate').write_text(wrapper)
            output = self.run(['exim4', '-C', str(stage / 'config-candidate'), '-bP', 'primary_hostname'])
            hostname = re.search(r'^primary_hostname = (\S+)$', output, re.M)
            if not hostname:
                raise ValueError('Cannot determine the configured server hostname')
            if action == 'forward-spam-policy' and mode != 'off':
                validate_condition(self.run, stage / 'config-candidate', stage)
            if action == 'notifications':
                if recipient.lower().rsplit('@', 1)[1] == hostname[1].lower():
                    raise ValueError('Notification destination must not use the server hostname')
                self.run(['exim4', '-C', str(stage / 'config-candidate'), '-bt', recipient])
                for localpart in ('root', 'postmaster', 'mailer-daemon'):
                    self.run(['exim4', '-C', str(stage / 'config-candidate'), '-bt',
                              localpart + '@' + hostname[1]])
        else:
            self.run(['spamassassin', '--lint', '--cf=' + candidate.decode()])
        if (path.read_bytes() if path.exists() else None) != previous:
            raise ValueError('Configuration changed during validation; reload the page')
        if active_before is not None and self.active.read_bytes() != active_before:
            raise ValueError('Exim configuration changed during validation; reload the page')
        self.replace(path, candidate, metadata)
        try:
            self.run(['exim4', '-bP', 'primary_hostname'] if exim_change
                     else ['spamassassin', '--lint'])
            self.run(service)
        except Exception as error:
            # Return failure even if the original service reload succeeds.
            if previous is None:
                path.unlink()
            else:
                self.replace(path, previous, metadata)
            try:
                self.run(service)
            except Exception:
                raise ValueError('Previous configuration restored; service recovery failed. '
                                 'Backup: ' + str(stage)) from error
            raise ValueError('Change failed; previous configuration restored. Backup: ' + str(stage)) from error
        return True


def main():
    # No runtime path overrides: hestiaweb may invoke this only through Hestia CLI.
    if os.geteuid() != 0:
        raise ValueError('Root privileges required')
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    with open('/run/lock/hestia-mail-security.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH if action == 'read' else fcntl.LOCK_EX)
        settings = Settings()
        if action == 'read' and len(sys.argv) == 2:
            print(json.dumps(settings.read()))
        elif action == 'notifications' and len(sys.argv) == 3:
            settings.change(action, sys.argv[2])
        elif action == 'validity-disable' and len(sys.argv) == 2:
            settings.change(action)
        elif action == 'forward-spam-policy' and len(sys.argv) == 3:
            settings.change(action, sys.argv[2])
        else:
            raise ValueError('Invalid mail security arguments')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
