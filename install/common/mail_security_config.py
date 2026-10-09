#!/usr/bin/env python3
"""Prepare mail security configuration without modifying live services or files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

INSTALL = Path(__file__).resolve().parents[1]
ROUTER = INSTALL / 'deb/exim/system-notifications.router'
VALIDITY = INSTALL / 'deb/spamassassin/zz-validity-disabled.cf'


def render_router(template, recipient):
    # Restrict the parameter to common mailbox syntax; it is inserted into Exim data.
    label = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@' + label + r'(?:\.' + label + r')+', recipient):
        raise ValueError('Invalid notification email address')
    block = ROUTER.read_text().replace('@NOTIFICATION_EMAIL@', recipient)
    pattern = r'^hestia_system_notifications:[ \t]*\n(?:[ \t]+[^\n]*\n|[ \t]*\n)*'
    matches = list(re.finditer(pattern, template, re.M))
    if matches:
        if len(matches) != 1:
            raise ValueError('Duplicate notification routers')
        existing = matches[0].group()
        # Do not replace an independently customized router with the same name.
        expected = block.splitlines()
        lines = [line.strip() for line in existing.splitlines() if line.strip()]
        if len(lines) != len(expected):
            raise ValueError('Existing notification router has custom options')
        for old, new in zip(lines, expected):
            if new.strip().startswith('data = '):
                if not old.startswith('data = '):
                    raise ValueError('Unexpected existing router format')
            elif old != new.strip():
                raise ValueError('Existing notification router has custom options')
        trailing = existing[len(existing.rstrip('\n\t ')):]
        return template[:matches[0].start()] + block.rstrip('\n') + trailing + template[matches[0].end():]
    updated, count = re.subn(r'^begin routers[ \t]*$',
                            lambda m: m.group() + '\n\n' + block,
                            template, flags=re.M)
    if count != 1:
        raise ValueError('Expected exactly one routers section')
    return updated


def redirect_include(principal, template_path, candidate_path):
    pattern = (r'(^[ \t]*\.include(?:_if_exists)?[ \t]+)' +
               re.escape(str(template_path)) + r'([ \t]*$)')
    updated, count = re.subn(pattern, lambda m: m.group(1) + str(candidate_path) + m.group(2),
                            principal, flags=re.M)
    if count != 1:
        raise ValueError('Expected exactly one direct include of the Exim template')
    return updated


def prepare(active, template, recipient, destination, antispam):
    active, template, destination, antispam = map(Path, (active, template, destination, antispam))
    if '\n' in str(destination) or '\r' in str(destination):
        raise ValueError('Invalid output directory')
    # Read and validate before creating any output. Templates remain site-specific.
    router_config = render_router(template.read_text(), recipient)
    candidate_template = destination.resolve() / 'template-candidata'
    principal = redirect_include(active.read_text(), template, candidate_template)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Output directory must be empty; backups will not be overwritten')
    destination.mkdir(mode=0o700, parents=False, exist_ok=True)
    destination.chmod(0o700)
    shutil.copy2(active, destination / 'config-anterior')
    shutil.copy2(template, destination / 'template-anterior')
    (destination / 'config-candidata').write_text(principal)
    candidate_template.write_text(router_config)
    shutil.copy2(VALIDITY, destination / 'zz-validity-disabled.cf')
    if antispam.exists():
        shutil.copy2(antispam, destination / 'validity-anterior')
    manifest = {
        'active_config': str(active), 'template_config': str(template),
        'antispam_config': str(antispam), 'notification_email': recipient,
        'original_sha256': {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (active, template)
        },
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notification-email', required=True)
    parser.add_argument('--active-config', type=Path, default=Path('/etc/exim4/exim4.conf'))
    parser.add_argument('--template-config', type=Path, default=Path('/etc/exim4/exim4.conf.template'))
    parser.add_argument('--antispam-config', type=Path,
                        default=Path('/etc/mail/spamassassin/zz-validity-disabled.cf'))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    try:
        destination = args.output_dir or Path(tempfile.mkdtemp(prefix='mail-security-', dir='/root'))
        result = prepare(args.active_config, args.template_config, args.notification_email,
                         destination, args.antispam_config)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    print('Prepared in:', result)
    print('Live configuration and services were not modified.')


if __name__ == '__main__':
    main()
