"""Change only Hestia's supported SpamAssassin header-scan size gate."""
import re


def scan_gate(template):
    start = re.search(r'^acl_check_data:[ \t]*$', template, re.M)
    if not start:
        raise ValueError('Expected the Hestia DATA ACL')
    end = re.search(r'^(?:acl_[a-z_]+:|begin )', template[start.end():], re.M)
    stop = start.end() + end.start() if end else len(template)
    section = template[start.end():stop]
    pattern = r'^[ \t]*condition[ \t]*=[ \t]*\$\{if < \{\$message_size\}\{([0-9]+[KM])\}\}[ \t]*$'
    matches = list(re.finditer(pattern, section, re.M))
    if len(matches) != 1:
        raise ValueError('Custom or ambiguous spam scan size gate; review manually')
    match = matches[0]
    following = section[match.end():]
    if not re.match(r'\s*condition\s*=\s*\$\{if eq\{\$acl_m1\}\{yes\}\{yes\}\{no\}\}\s*'
                    r'spam\s*=\s*debian-spamd:true/defer_ok\s*', following):
        raise ValueError('Custom spam scanning conditions; review manually')
    return start.end() + match.start(1), start.end() + match.end(1)


def render_scan_limit(template, mib):
    if not isinstance(mib, str) or not re.fullmatch(r'[1-9][0-9]?', mib) or not 1 <= int(mib) <= 25:
        raise ValueError('Spam scan size must be an integer between 1 and 25 MiB')
    start, stop = scan_gate(template)
    return template[:start] + str(int(mib)) + 'M' + template[stop:]


def validate_scan_limit(run, config, mib):
    from mail_forwarding_guard import expansion_result
    size = int(mib) * 1024 * 1024
    for value, expected in ((size - 1, 'yes'), (size, 'no'), (size + 1, 'no')):
        expression = '${if < {' + str(value) + '}{' + mib + 'M}{yes}{no}}\n'
        if expansion_result(run(['exim4', '-C', str(config), '-be'], input_text=expression)) != expected:
            raise ValueError('Exim spam scan size boundary check failed')
