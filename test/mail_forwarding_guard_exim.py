#!/usr/bin/env python3
"""Run on the VPS as root: validate a candidate against Exim without installing it."""
from pathlib import Path
import os
import sys
import subprocess
import tempfile
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'install/common'))
from mail_forwarding_guard import render_forwarding_guard, validate_condition
from mail_security_config import redirect_include
from mail_security_settings import Settings

def diagnostic_command(args, input_text=None):
    # Only the dedicated root-only preview exposes controlled expansion diagnostics.
    # The production web helper keeps service output private.
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=60)
    if result.returncode or ('-be' in args and 'Failed to expand' in result.stdout):
        if '-be' in args:
            detail = (result.stdout + result.stderr).strip()[:2000]
            raise ValueError('Exim expansion error: ' + detail)
        raise ValueError('Candidate configuration did not parse; inspect Exim locally.')
    return result.stdout


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run as root on the VPS; active configuration is not changed.')
    active = Path('/etc/exim4/exim4.conf')
    template = Path('/etc/exim4/exim4.conf.template')
    with tempfile.TemporaryDirectory(prefix='hestia-forward-validation-') as directory:
        stage = Path(directory)
        candidate = stage / 'template'
        candidate.write_text(render_forwarding_guard(template.read_text(), 'meetlogistic.com'))
        wrapper = stage / 'config'
        wrapper.write_text(redirect_include(active.read_text(), template, candidate))
        Settings.command(['exim4', '-C', str(wrapper), '-bP', 'primary_hostname'])
        validate_condition(diagnostic_command, wrapper, stage)
    print('Exim: candidate parsed; score boundary, unscanned mail, missing mailbox and disabled antispam checks passed. No active files changed.')
