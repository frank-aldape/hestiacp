#!/usr/bin/env python3
"""Summarize Exim delivery metadata without changing mail or exposing message content."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import re


def analyze(directory, days=7, now=None):
    now = now or datetime.now()
    cutoff = now - timedelta(days=days)
    totals, daily, domains, protocols, codes, reasons, local_domains, local_daily = (Counter() for _ in range(8))
    files, errors = [], []
    oldest, newest = None, None
    for path in sorted(Path(directory).glob('mainlog*')):
        if not path.is_file():
            continue
        try:
            opener = gzip.open if path.suffix == '.gz' else open
            with opener(path, 'rt', errors='replace') as stream:
                files.append(path.name)
                for line in stream:
                    try:
                        stamp = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        continue
                    if not cutoff <= stamp <= now:
                        continue
                    fields = line.split()
                    if len(fields) < 5:
                        continue
                    oldest = min(oldest, stamp) if oldest else stamp
                    newest = max(newest, stamp) if newest else stamp
                    event = fields[3]
                    if event == '<=':
                        totals['accepted_messages'] += 1
                        protocol = re.search(r'\bP=(\S+)', line)
                        protocol = protocol[1] if protocol else 'unknown'
                        protocols[protocol] += 1
                        if fields[4] == '<>':
                            totals['bounce_messages'] += 1
                        if protocol in ('esmtpa', 'esmtpsa', 'smtp', 'esmtp', 'esmtps') and re.search(r'\bA=', line):
                            totals['authenticated_submissions'] += 1
                            daily[stamp.date().isoformat()] += 1
                            domain = fields[4].rsplit('@', 1)[-1].lower()
                            if re.fullmatch(r'[a-z0-9.-]+', domain):
                                domains[domain] += 1
                        elif protocol == 'local':
                            totals['local_submissions'] += 1
                            if fields[4] != '<>':
                                local_daily[stamp.date().isoformat()] += 1
                                domain = fields[4].rsplit('@', 1)[-1].lower()
                                if re.fullmatch(r'[a-z0-9.-]+', domain):
                                    local_domains[domain] += 1
                    if event == '=>' and re.search(r'\bT=(remote_smtp|remote_forwarded_smtp|smtp_relay_smtp)\b', line):
                        totals['remote_delivery_events'] += 1
                        if 'T=remote_forwarded_smtp' in line:
                            totals['forwarded_delivery_events'] += 1
                    if event not in ('**', '=='):
                        continue
                    totals['permanent_failure_events' if event == '**' else 'deferred_events'] += 1
                    status = re.search(r'\b([45][0-9]{2})[ -]([45]\.[0-9]+\.[0-9]+)', line)
                    if status:
                        codes[status[1] + ' ' + status[2]] += 1
                    lower = line.lower()
                    for name, pattern in (
                        ('authentication', r'unauthenticated|spf|dkim|dmarc'),
                        ('reputation_or_spam', r'reputation|likely.*spam|unsolicited|blacklist|blocklist'),
                        ('rate_limit', r'rate.?limit|too many|throttl'),
                        ('recipient_missing', r'no mailbox|unknown user|user unknown|5\.1\.1'),
                        ('long_lines', r'lines too long'),
                    ):
                        if re.search(pattern, lower):
                            reasons[name] += 1
        except (OSError, EOFError):
            errors.append(path.name)
    return {
        'requested_days': days,
        'available_period': {'first': oldest.isoformat() if oldest else None,
                             'last': newest.isoformat() if newest else None},
        'logs_read': files, 'unreadable_or_incomplete_logs': errors,
        'totals': dict(totals), 'accepted_protocols': dict(protocols),
        'authenticated_messages_by_day': dict(sorted(daily.items())),
        'top_authenticated_sender_domains': dict(domains.most_common(15)),
        'local_messages_by_day_excluding_bounces': dict(sorted(local_daily.items())),
        'top_local_sender_domains_excluding_bounces': dict(local_domains.most_common(15)),
        'failure_smtp_codes': dict(codes.most_common(15)),
        'failure_reason_hints': dict(reasons),
        'notes': ['Delivery/failure events may include multiple recipients and repeated attempts.',
                  'SMTP acceptance does not prove inbox placement.',
                  'Reason hints are log text matches, not a diagnosis of Gmail reputation.',
                  'Available logs may cover less than the requested period.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, choices=range(1, 31), default=7)
    parser.add_argument('--log-directory', type=Path, default=Path('/var/log/exim4'))
    args = parser.parse_args()
    print(json.dumps(analyze(args.log_directory, args.days), indent=2))
