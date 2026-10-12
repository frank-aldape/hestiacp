#!/usr/bin/env python3
"""Private minute samples and sustained alerts for the shared webmail FPM pool."""
import datetime
from email.message import EmailMessage
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

from webmail_fpm_status import read_status

CRON = '# Hestia webmail monitor (managed)\n* * * * * root /usr/local/hestia/bin/v-update-sys-webmail-monitor 2>&1 | /usr/bin/logger -t hestia-webmail-monitor\n'
DEFAULTS = {'enabled': False, 'recipient': '',
            'queue_threshold': 1, 'consecutive': 3, 'cooldown_minutes': 60}


def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.monitor-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def validate(settings):
    if type(settings.get('enabled')) is not bool:
        raise ValueError('Invalid monitor switch')
    email = settings.get('recipient', '')
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+', email):
        raise ValueError('Invalid alert recipient')
    for key, limits in [('queue_threshold', (1, 10000)), ('consecutive', (2, 30)),
                        ('cooldown_minutes', (5, 1440))]:
        if type(settings.get(key)) is not int or not limits[0] <= settings[key] <= limits[1]:
            raise ValueError('Invalid alert setting: ' + key)
    return {key: settings[key] for key in DEFAULTS}


def transition(previous, sample, settings):
    metrics = sample['metrics']
    old = previous.get('latest') or {}
    old_metrics = old.get('metrics') or {}
    contiguous = 0 < sample['time'] - old.get('time', 0) <= 90
    same_epoch = metrics and metrics['start time'] == old_metrics.get('start time')
    limit_delta = max(0, metrics['max children reached'] - old_metrics.get('max children reached', 0)) if same_epoch else 0
    reason = sample['error']
    if not reason and (metrics['listen queue'] >= settings['queue_threshold'] or limit_delta):
        reason = 'capacity_pressure'
    streak = previous.get('streak', 0) + 1 if reason and contiguous and reason == previous.get('reason') and (reason != 'capacity_pressure' or same_epoch) else int(bool(reason))
    state = {'latest': sample, 'reason': reason, 'streak': streak,
             'last_alert': previous.get('last_alert', 0),
             'last_alert_reason': previous.get('last_alert_reason'),
             'notification_error': False}
    due = bool(reason and streak >= settings['consecutive'] and
               sample['time'] - state['last_alert'] >= settings['cooldown_minutes'] * 60)
    return state, due


class Monitor:
    def __init__(self, root=Path('/var/lib/hestia-webmail-monitor'),
                 config=Path('/etc/hestiacp/webmail-monitor.json'),
                 cron=Path('/etc/cron.d/hestia-webmail-monitor'), probe=read_status,
                 active=None, notify=None):
        self.root, self.config, self.cron = map(Path, (root, config, cron))
        self.probe = probe
        self.active = active or self.service_active
        self.notify = notify or self.send_alert

    @staticmethod
    def service_active():
        return subprocess.run(['systemctl', 'is-active', '--quiet', 'php8.3-fpm'],
                              timeout=10, capture_output=True).returncode == 0

    @staticmethod
    def send_alert(settings, sample, reason):
        message = EmailMessage()
        hostname = socket.getfqdn()
        if not re.fullmatch(r'[a-zA-Z0-9.-]+', hostname):
            hostname = 'localhost'
        message['From'] = 'Monitor Webmail <root@' + hostname + '>'
        message['To'] = settings['recipient']
        message['Subject'] = '[Hestia] Alerta de webmail: ' + reason
        message.set_content('Servidor: ' + hostname + '\nMotivo: ' + reason +
                            '\nMuestras consecutivas: ' + str(settings['consecutive']) +
                            '\nMétricas agregadas:\n' + json.dumps(sample, indent=2) +
                            '\nPanel: /list/mail/performance/\n')
        # Queue locally; successful return means accepted by Exim, not delivered remotely.
        result = subprocess.run(['/usr/sbin/exim4', '-i', '-odq', '-t'],
                                input=message.as_bytes(), capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('Exim did not accept the monitor notification')

    def settings(self):
        return validate(json.loads(self.config.read_text())) if self.config.exists() else dict(DEFAULTS)

    def state(self):
        path = self.root / 'state.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def configure(self, settings):
        settings = validate(settings)
        if self.config.is_symlink() or self.cron.is_symlink():
            raise ValueError('Symlinked monitoring configuration requires manual review')
        old_cron = self.cron.read_text() if self.cron.exists() else None
        if old_cron is not None and old_cron != CRON:
            raise ValueError('Existing cron file is custom; it will not be overwritten')
        old_config = self.config.read_text() if self.config.exists() else None
        if old_config:
            atomic(self.root / ('settings-backup-' + str(time.time_ns()) + '.json'), old_config)
        try:
            if settings['enabled']:
                atomic(self.cron, CRON, 0o644)
            else:
                self.cron.unlink(missing_ok=True)
            atomic(self.config, json.dumps(settings) + '\n')
            # Start a new streak after policy changes; retain history/cooldown.
            state = self.state()
            state.update(reason=None, streak=0)
            atomic(self.root / 'state.json', json.dumps(state) + '\n')
        except Exception:
            if old_cron is None:
                self.cron.unlink(missing_ok=True)
            else:
                atomic(self.cron, old_cron, 0o644)
            if old_config is None:
                self.config.unlink(missing_ok=True)
            else:
                atomic(self.config, old_config)
            raise

    def sample(self, now=None):
        settings = self.settings()
        if not settings['enabled']:
            return
        now = int(time.time() if now is None else now)
        sample = {'time': now, 'metrics': None, 'error': None}
        try:
            if not self.active():
                sample['error'] = 'service_down'
            else:
                sample['metrics'] = self.probe()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            sample['error'] = 'probe_failed'
        state, due = transition(self.state(), sample, settings)
        if due:
            try:
                self.notify(settings, sample, state['reason'])
                state.update(last_alert=now, last_alert_reason=state['reason'])
            except (OSError, ValueError, subprocess.TimeoutExpired):
                state['notification_error'] = True
        atomic(self.root / 'state.json', json.dumps(state) + '\n')
        day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
        path = self.root / (day.isoformat() + '.jsonl')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(sample) + '\n')
        cutoff = (day - datetime.timedelta(days=6)).isoformat()
        for old in self.root.glob('????-??-??.jsonl'):
            if old.stem < cutoff:
                old.unlink()

    def read(self, hours=24, now=None):
        if hours not in (1, 24, 168):
            raise ValueError('Unsupported history period')
        now = int(time.time() if now is None else now)
        history = []
        for path in sorted(self.root.glob('????-??-??.jsonl')):
            with path.open() as stream:
                for line in stream:
                    sample = json.loads(line)
                    if now - hours * 3600 <= sample['time'] <= now:
                        history.append(sample)
        # Bound the web response; daily files hold a maximum seven-day retention window.
        return {'config': self.settings(), 'state': self.state(),
                'history': history[-10080:], 'now': now}


def main():
    if os.geteuid() != 0:
        raise ValueError('Root privileges required')
    monitor = Monitor()
    monitor.root.mkdir(mode=0o700, exist_ok=True)
    monitor.root.chmod(0o700)
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    with (monitor.root / 'lock').open('a') as lock:
        operation = fcntl.LOCK_SH if action == 'read' else fcntl.LOCK_EX
        try:
            fcntl.flock(lock, operation | (fcntl.LOCK_NB if action == 'sample' else 0))
        except BlockingIOError:
            return
        if action == 'read' and len(sys.argv) in (2, 3):
            print(json.dumps(monitor.read(int(sys.argv[2]) if len(sys.argv) == 3 else 24)))
        elif action == 'sample' and len(sys.argv) == 2:
            monitor.sample()
        elif action == 'configure' and len(sys.argv) == 7 and sys.argv[2] in ('yes', 'no'):
            monitor.configure({'enabled': sys.argv[2] == 'yes', 'recipient': sys.argv[3],
                               'queue_threshold': int(sys.argv[4]), 'consecutive': int(sys.argv[5]),
                               'cooldown_minutes': int(sys.argv[6])})
        else:
            raise ValueError('Invalid monitor arguments')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Webmail monitor: ' + str(error), file=sys.stderr)
        sys.exit(1)
