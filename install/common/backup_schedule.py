#!/usr/bin/env python3
"""Manage opt-in per-user schedules without disabling Hestia's manual backups."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from webmail_monitor import atomic

JOB = '* * * * * root /usr/local/hestia/bin/v-update-sys-backup-schedules 2>&1 | /usr/bin/logger -t hestia-backup-schedules\n'
CRON = '# Hestia user backup schedules (managed)\n' + JOB
LEGACY_COMMAND = 'sudo /usr/local/hestia/bin/v-backup-users'
MANUAL = {'frequency': 'manual', 'hour': 5, 'minute': 10, 'weekday': 0, 'day': 1}


def validate(policy):
    if policy.get('frequency') not in ('manual', 'daily', 'weekly', 'monthly'):
        raise ValueError('Invalid backup frequency')
    for key, maximum, minimum in [('hour', 23, 0), ('minute', 59, 0), ('weekday', 6, 0), ('day', 28, 1)]:
        if type(policy.get(key)) is not int or not minimum <= policy[key] <= maximum:
            raise ValueError('Invalid backup schedule: ' + key)
    return {key: policy[key] for key in MANUAL}


def legacy_schedule(text):
    candidates = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        if line.lstrip().startswith('#'):
            continue
        if re.search(r'(?<![\w-])v-backup-users(?![\w-])', line):
            candidates.append((index, line))
    if not candidates:
        return None, dict(MANUAL)
    if len(candidates) != 1:
        raise ValueError('Multiple legacy backup jobs require manual review')
    index, line = candidates[0]
    if re.search(r'^[ \t]*(?:CRON_TZ|TZ)[ \t]*=', text, re.M):
        raise ValueError('Custom backup cron timezone requires manual review')
    fields = line.strip().split(None, 5)
    if len(fields) != 6 or fields[5] != LEGACY_COMMAND:
        raise ValueError('Custom legacy backup command requires manual review')
    minute, hour, day, month, weekday = fields[:5]
    if not minute.isdigit() or not hour.isdigit() or month != '*':
        raise ValueError('Custom legacy backup frequency requires manual review')
    result = {**MANUAL, 'hour': int(hour), 'minute': int(minute)}
    if day == '*' and weekday == '*':
        result['frequency'] = 'daily'
    elif day == '*' and weekday.isdigit():
        result.update(frequency='weekly', weekday=int(weekday) % 7)
        if not 0 <= int(weekday) <= 7:
            raise ValueError('Invalid legacy weekday')
    elif day.isdigit() and weekday == '*':
        result.update(frequency='monthly', day=int(day))
    else:
        raise ValueError('Custom legacy backup calendar requires manual review')
    return index, validate(result)


def due(policy, now):
    if policy['frequency'] == 'manual' or (now.hour, now.minute) != (policy['hour'], policy['minute']):
        return False
    if policy['frequency'] == 'weekly' and (now.weekday() + 1) % 7 != policy['weekday']:
        return False
    if policy['frequency'] == 'monthly' and now.day != policy['day']:
        return False
    return True


class Schedules:
    def __init__(self, users=Path('/usr/local/hestia/data/users'),
                 config=Path('/etc/hestiacp/backup-schedules.json'),
                 root=Path('/var/lib/hestia-backup-schedules'),
                 cron=Path('/etc/cron.d/hestia-backup-schedules'), run=None):
        self.users, self.config, self.root, self.cron = map(Path, (users, config, root, cron))
        self.run = run or self.command

    @staticmethod
    def command(args, input_text=None):
        return subprocess.run(args, input=input_text, capture_output=True, text=True,
                              timeout=30, env={**os.environ, 'LC_ALL': 'C'})

    def legacy_cron(self):
        result = self.run(['crontab', '-u', 'hestiaweb', '-l'])
        if result.returncode and 'no crontab for' not in result.stderr.lower():
            raise ValueError('Cannot read the hestiaweb crontab')
        return result.stdout if not result.returncode else ''

    def user_list(self):
        result = []
        for path in sorted(self.users.iterdir()):
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', path.name) or not (path / 'user.conf').is_file():
                continue
            text = (path / 'user.conf').read_text()
            quota = re.search(r"^BACKUPS='([^']*)'", text, re.M)
            suspended = re.search(r"^SUSPENDED='yes'", text, re.M) is not None
            result.append({'user': path.name, 'retention': quota[1] if quota else '', 'suspended': suspended})
        return result

    def settings(self):
        if self.config.exists():
            data = json.loads(self.config.read_text())
            return {'default': validate(data['default']),
                    'users': {key: validate(value) for key, value in data['users'].items()}}
        _, default = legacy_schedule(self.legacy_cron())
        return {'default': default, 'users': {}}

    def read(self):
        try:
            settings = self.settings()
            supported = True
        except ValueError:
            settings = {'default': dict(MANUAL), 'users': {}}
            supported = False
        state = self.state()
        users = self.user_list()
        for user in users:
            user['policy'] = settings['users'].get(user['user'], settings['default'])
            user['last_attempt'] = state.get(user['user'])
        return {'supported': supported, 'managed': self.config.exists(),
                'dispatcher_installed': self.cron.exists() and self.cron.read_text() == CRON,
                'users': users, 'timezone': datetime.datetime.now().astimezone().tzname()}

    def state(self):
        path = self.root / 'state.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def configure(self, user, policy):
        policy = validate(policy)
        if user not in [item['user'] for item in self.user_list()]:
            raise ValueError('Existing Hestia user required')
        if self.config.is_symlink() or self.cron.is_symlink():
            raise ValueError('Symlinked scheduling configuration requires manual review')
        old_cron = self.cron.read_text() if self.cron.exists() else None
        if old_cron not in (None, CRON):
            raise ValueError('The dispatcher cron is custom; it will not be overwritten')
        old_settings = self.config.read_text() if self.config.exists() else None
        original = self.legacy_cron()
        index, _ = legacy_schedule(original)
        settings = self.settings()
        if old_settings and index is not None:
            raise ValueError('A legacy backup job reappeared; review it before changing schedules')
        settings['users'][user] = policy
        candidate = ''.join(line for number, line in enumerate(original.splitlines(keepends=True)) if number != index)
        backup = self.root / ('change-' + str(time.time_ns()))
        backup.mkdir(mode=0o700)
        atomic(backup / 'hestiaweb-crontab-before', original)
        if old_settings:
            atomic(backup / 'settings-before.json', old_settings)
        if self.legacy_cron() != original:
            raise ValueError('The crontab changed during preparation; retry')
        changed_crontab = False
        try:
            atomic(self.config, json.dumps(settings) + '\n')
            atomic(self.cron, CRON, 0o644)
            if candidate != original:
                result = self.run(['crontab', '-u', 'hestiaweb', '-'], input_text=candidate)
                if result.returncode:
                    raise ValueError('Cannot replace the legacy backup schedule')
                changed_crontab = True
        except Exception:
            if old_settings is None:
                self.config.unlink(missing_ok=True)
            else:
                atomic(self.config, old_settings)
            if old_cron is None:
                self.cron.unlink(missing_ok=True)
            else:
                atomic(self.cron, old_cron, 0o644)
            if changed_crontab:
                self.run(['crontab', '-u', 'hestiaweb', '-'], input_text=original)
            raise

    def dispatch(self, now=None):
        if not self.config.exists():
            return
        now = now or datetime.datetime.now().astimezone()
        settings = self.settings()
        state = self.state()
        stamp = now.strftime('%Y-%m-%dT%H:%M')
        for user in self.user_list():
            name = user['user']
            policy = settings['users'].get(name, settings['default'])
            if user['suspended'] or user['retention'] in ('0', '') or not due(policy, now):
                continue
            if state.get(name, {}).get('slot') == stamp:
                continue
            # Record intent before enqueuing: replaying the minute must not duplicate work.
            state[name] = {'slot': stamp, 'time': int(now.timestamp()), 'status': 'dispatching'}
            atomic(self.root / 'state.json', json.dumps(state) + '\n')
            try:
                result = self.run(['/usr/local/hestia/bin/v-schedule-user-backup', name])
                state[name]['exit_code'] = result.returncode
                state[name]['status'] = 'queued' if result.returncode == 0 else ('already_scheduled' if result.returncode == 4 else 'failed')
            except (OSError, subprocess.TimeoutExpired):
                state[name]['status'] = 'failed'
            atomic(self.root / 'state.json', json.dumps(state) + '\n')


def main():
    if os.geteuid() != 0:
        raise ValueError('Root privileges required')
    schedules = Schedules()
    schedules.root.mkdir(mode=0o700, exist_ok=True)
    schedules.root.chmod(0o700)
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    with (schedules.root / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if action == 'read' else fcntl.LOCK_EX) |
                        (fcntl.LOCK_NB if action == 'dispatch' else 0))
        except BlockingIOError:
            return
        if action == 'read' and len(sys.argv) == 2:
            print(json.dumps(schedules.read()))
        elif action == 'dispatch' and len(sys.argv) == 2:
            schedules.dispatch()
        elif action == 'configure' and len(sys.argv) == 8:
            schedules.configure(sys.argv[2], {'frequency': sys.argv[3], 'hour': int(sys.argv[4]),
                                'minute': int(sys.argv[5]), 'weekday': int(sys.argv[6]), 'day': int(sys.argv[7])})
        else:
            raise ValueError('Invalid backup scheduling arguments')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Backup schedules: ' + str(error), file=sys.stderr)
        sys.exit(1)
