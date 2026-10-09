"""Manage an opt-in router that keeps scored spam in a real local mailbox."""
from pathlib import Path
import re

ASSET = Path(__file__).resolve().parents[1] / 'deb/exim/spam-forwarding-guard.router'
BEGIN = '# BEGIN HESTIA SPAM FORWARDING GUARD'
END = '# END HESTIA SPAM FORWARDING GUARD'


def policy_domains(value):
    if value in ('off', 'all'):
        return value, []
    if len(value) > 4096:
        raise ValueError('Too many forwarding policy domains')
    domains = value.lower().split(',')
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if not domains or any(len(domain) > 253 or not re.fullmatch(label + r'(?:\.' + label + r')+', domain)
                          for domain in domains):
        raise ValueError('Use all, off, or a comma-separated list of domain names')
    return 'selected', sorted(set(domains))


def block(mode, domains):
    value = '+local_domains' if mode == 'all' else ' : '.join(domains)
    return ASSET.read_text().replace('@DOMAINS@', value)


def managed_block(template):
    pattern = re.escape(BEGIN) + r'\n.*?\n' + re.escape(END) + r'\n'
    matches = list(re.finditer(pattern, template, re.S))
    if template.count(BEGIN) != len(matches) or template.count(END) != len(matches) or len(matches) > 1:
        raise ValueError('Forwarding protection markers are incomplete or duplicated')
    if not matches:
        if re.search(r'^hestia_spam_forward_guard:', template, re.M):
            raise ValueError('Existing forwarding guard is not managed by this tool')
        return None, {'mode': 'off', 'domains': []}
    match = matches[0]
    value = re.search(r'^  domains = (.+)$', match.group(), re.M)
    if not value:
        raise ValueError('Unsupported forwarding guard')
    policy = 'all' if value[1] == '+local_domains' else value[1].replace(' : ', ',')
    mode, domains = policy_domains(policy)
    if mode == 'off' or match.group() != block(mode, domains):
        raise ValueError('Forwarding guard contains custom options')
    aliases = re.search(r'^aliases:', template, re.M)
    if not aliases or match.end() > aliases.start():
        raise ValueError('Forwarding guard must precede aliases')
    return match, {'mode': mode, 'domains': domains}


def forwarding_state(template):
    return managed_block(template)[1]


def render_forwarding_guard(template, policy):
    mode, domains = policy_domains(policy)
    existing, _ = managed_block(template)
    if existing:
        if mode == 'off':
            return template[:existing.start()] + template[existing.end():]
        return template[:existing.start()] + block(mode, domains) + template[existing.end():]
    if mode == 'off':
        return template
    if len(re.findall(r'^aliases:', template, re.M)) != 1 or not re.search(r'^local_spam_delivery:', template, re.M):
        raise ValueError('Expected the Hestia aliases router and local spam transport')
    return re.sub(r'^aliases:', lambda m: block(mode, domains) + m.group(), template, count=1, flags=re.M)


def validate_condition(run, config, stage):
    """Exercise Exim's expansion engine with private fixtures, without sending mail."""
    prefix = ['exim4', '-C', str(config), '-be']
    threshold_text = run(prefix + ['SPAM_SCORE']).strip()
    if not re.fullmatch(r'-?[0-9]+', threshold_text):
        raise ValueError('Expected an integer Exim spam threshold')
    threshold = int(threshold_text)
    fixture = stage / 'guard-fixture'
    domain = fixture / 'fixture.example.org'
    domain.mkdir(parents=True, mode=0o700)
    marker = domain / 'antispam'
    marker.touch()
    (domain / 'passwd').write_text('mailbox:fixture\n')
    expression = re.search(r'^  condition = (.+)$', ASSET.read_text(), re.M)[1]
    expression = expression.replace('/etc/exim4/domains/', str(fixture) + '/')
    expression = expression.replace('$domain', 'fixture.example.org').replace('$local_part', 'mailbox')
    expression = expression.replace('SPAM_SCORE', str(threshold))

    def check(score, expected, account='mailbox'):
        test = expression.replace('$acl_m2', str(score)).replace('{mailbox}', '{' + account + '}')
        if run(prefix + [test]).strip() != expected:
            raise ValueError('Exim forwarding protection expansion check failed')

    for score in ('', threshold - 1, threshold):
        check(score, 'no')
    check(threshold + 1, 'yes')
    check(threshold + 1, 'no', 'missing')
    marker.unlink()
    check(threshold + 1, 'no')
