#!/usr/bin/env python3
"""Read aggregate www-pool metrics over its optional local status socket."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

SOCKET = Path('/run/php/hestia-webmail-status.sock')
STATUS_PATH = '/__hestia_internal_fpm_status'
COUNTERS = (
    'start time', 'start since', 'accepted conn', 'listen queue',
    'max listen queue', 'listen queue len', 'idle processes', 'active processes',
    'total processes', 'max active processes', 'max children reached', 'slow requests',
)


def parse_response(response):
    headers, separator, body = response.replace('\r\n', '\n').partition('\n\n')
    if not separator:
        raise ValueError('Missing FastCGI response headers')
    for line in headers.splitlines():
        if line.lower().startswith('status:') and line.split(':', 1)[1].strip().split()[0] != '200':
            raise ValueError('PHP-FPM status request failed')
    data = json.loads(body)
    if not isinstance(data, dict) or data.get('pool') != 'www':
        raise ValueError('Expected metrics for the www pool')
    result = {'pool': 'www', 'process manager': data.get('process manager')}
    if result['process manager'] not in ('ondemand', 'dynamic', 'static'):
        raise ValueError('Invalid process manager')
    for key in COUNTERS:
        value = data.get(key)
        if type(value) is not int or value < 0:
            raise ValueError('Missing or invalid counter: ' + key)
        result[key] = value
    # Never emit per-request URLs, authenticated users or arbitrary backend fields.
    return result


def read_status():
    if os.geteuid() != 0:
        raise ValueError('Run this diagnostic as root')
    metadata = SOCKET.lstat()
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_mode & 0o007:
        raise ValueError('Expected a Unix socket without permissions for other users')
    environment = {
        'PATH': '/usr/bin:/bin', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'json',
        'SCRIPT_NAME': STATUS_PATH, 'SCRIPT_FILENAME': STATUS_PATH,
        'REQUEST_URI': STATUS_PATH + '?json', 'SERVER_PROTOCOL': 'HTTP/1.1',
        'GATEWAY_INTERFACE': 'CGI/1.1', 'REMOTE_ADDR': '127.0.0.1',
        'SERVER_NAME': 'localhost', 'SERVER_PORT': '0', 'CONTENT_LENGTH': '0',
    }
    response = subprocess.run(
        ['/usr/bin/cgi-fcgi', '-bind', '-connect', str(SOCKET)],
        env=environment, input='', capture_output=True, text=True, timeout=10,
    )
    if response.returncode:
        raise ValueError('Cannot query the PHP-FPM status socket')
    return parse_response(response.stdout)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise ValueError('This diagnostic takes no arguments')
        print(json.dumps(read_status(), indent=2))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Webmail metrics: ' + str(error), file=sys.stderr)
        sys.exit(1)
