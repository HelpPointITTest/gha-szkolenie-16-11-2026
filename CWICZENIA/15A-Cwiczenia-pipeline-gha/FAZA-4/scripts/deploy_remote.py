#!/usr/bin/env python3
"""Deploy the published image over SSH; credentials travel only through stdin."""

import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time


# This program contains no credentials. Only its source is passed in the SSH
# command; the runtime configuration, including secrets, uses encrypted stdin.
REMOTE_PROGRAM = r'''
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

configuration = json.load(sys.stdin)
secrets = (configuration['dockerhub_token'], configuration['api_key'])
container = 'pogoda_api_faza4_' + configuration['environment']
docker_config = None


def safe_message(value):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '***')
    return text


def run(arguments, *, input_text=None, timeout=60, environment=None):
    result = subprocess.run(
        arguments, input=input_text, text=True, capture_output=True,
        timeout=timeout, env=environment,
    )
    if result.returncode:
        # Do not include command arguments or unfiltered process output.
        detail = safe_message(result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f'{arguments[0]} failed ({result.returncode}): {detail}')
    return result.stdout.strip()


def interrupted(signum, frame):
    raise InterruptedError('Remote deployment interrupted.')


for event in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
    signal.signal(event, interrupted)

try:
    print('Waiting for cloud-init and Docker.', flush=True)
    run(['cloud-init', 'status', '--wait'], timeout=480)
    run(['docker', 'info', '--format', '{{.ServerVersion}}'], timeout=30)

    docker_config = tempfile.mkdtemp(prefix='faza4-docker-config-')
    os.chmod(docker_config, 0o700)
    runtime_environment = os.environ.copy()
    runtime_environment['DOCKER_CONFIG'] = docker_config
    runtime_environment['OPENWEATHER_API_KEY'] = configuration['api_key']

    print('Logging in and pulling the published image by digest.', flush=True)
    run(
        ['docker', 'login', '--username', configuration['dockerhub_username'], '--password-stdin'],
        input_text=configuration['dockerhub_token'] + '\n',
        environment=runtime_environment,
    )
    # A failed pull leaves the current application running.
    run(['docker', 'pull', configuration['image_reference']],
        timeout=180, environment=runtime_environment)

    previous = run(
        ['docker', 'container', 'ls', '--all', '--quiet', '--filter', f'name=^/{container}$'],
        environment=runtime_environment,
    )
    if previous:
        run(['docker', 'rm', '--force', previous], environment=runtime_environment)
    run(
        ['docker', 'run', '-d', '--name', container, '--restart', 'unless-stopped',
         '-p', '80:8080', '-e', 'OPENWEATHER_API_KEY', configuration['image_reference']],
        environment=runtime_environment,
    )

    ready = False
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1/healthz', timeout=5) as response:
                ready = response.status == 200
        except (OSError, urllib.error.URLError):
            ready = False
        if ready:
            break
        time.sleep(2)
    if not ready:
        raise RuntimeError('Application /healthz did not become ready within 90 seconds.')
    running = run(
        ['docker', 'inspect', '--format', '{{.State.Running}}', container],
        environment=runtime_environment,
    )
    if running != 'true':
        raise RuntimeError('The deployment container is not running.')
    print('The deployment container is running and /healthz responds over HTTP.', flush=True)
except Exception as error:
    print('::error::' + safe_message(error), file=sys.stderr, flush=True)
    sys.exit(1)
finally:
    if docker_config is not None:
        try:
            result = subprocess.run(
                ['docker', 'logout'], text=True, capture_output=True,
                timeout=30, env={**os.environ, 'DOCKER_CONFIG': docker_config},
            )
            if result.returncode:
                print('Docker logout failed; removing the isolated credential directory.', file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired):
            print('Docker logout unavailable; removing the isolated credential directory.', file=sys.stderr)
        finally:
            shutil.rmtree(docker_config)
            print('Removed temporary Docker Hub credentials from the VM.', flush=True)
'''


def required(name):
    value = os.environ.get(name, '')
    if not value.strip():
        raise ValueError(f'Missing configuration: {name}.')
    return value


def main():
    environment = required('TARGET_ENVIRONMENT')
    if environment not in ('dev', 'prod'):
        raise ValueError('TARGET_ENVIRONMENT must be dev or prod.')
    host = required('DEPLOY_HOST')
    ipaddress.IPv4Address(host)
    droplet_id = required('DROPLET_ID')
    if not re.fullmatch(r'[1-9][0-9]*', droplet_id):
        raise ValueError('DROPLET_ID must be a positive integer.')
    image_reference = required('IMAGE_REFERENCE')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}', image_reference):
        raise ValueError('IMAGE_REFERENCE must be a Docker Hub image pinned by sha256 digest.')

    private_key = Path(required('SSH_PRIVATE_KEY_FILE'))
    key_status = private_key.lstat()
    if not stat.S_ISREG(key_status.st_mode) or stat.S_IMODE(key_status.st_mode) & 0o077:
        raise ValueError('The SSH private key must be a regular file accessible only to its owner (0600).')
    known_hosts = Path(required('SSH_KNOWN_HOSTS_FILE'))
    known_hosts.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if known_hosts.is_symlink() or (known_hosts.exists() and not stat.S_ISREG(known_hosts.lstat().st_mode)):
        raise ValueError('SSH_KNOWN_HOSTS_FILE must be a regular file.')
    descriptor = os.open(known_hosts, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    os.close(descriptor)
    known_hosts.chmod(0o600)

    configuration = {
        'environment': environment,
        'image_reference': image_reference,
        'dockerhub_username': required('DOCKERHUB_USERNAME'),
        'dockerhub_token': required('DOCKERHUB_TOKEN'),
        'api_key': required('OPENWEATHER_API_KEY'),
    }
    ssh = [
        'ssh', '-T', '-i', str(private_key),
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
        '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
        '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', f'UserKnownHostsFile={known_hosts}',
        '-o', 'GlobalKnownHostsFile=/dev/null',
        '-o', f'HostKeyAlias=faza4-{environment}-droplet-{droplet_id}',
        '-o', 'LogLevel=ERROR', f'root@{host}',
    ]
    # SSH needs only the key file. Do not pass inherited tokens to its process.
    process_environment = {
        name: value for name, value in os.environ.items()
        if not any(part in name.upper() for part in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'PRIVATE_KEY', 'TF_VAR_'))
    }
    print(f'Connecting to the {environment} Droplet over SSH.', flush=True)
    deadline = time.monotonic() + 180
    while True:
        try:
            probe = subprocess.run(
                ssh + ['true'], capture_output=True, text=True,
                timeout=20, env=process_environment,
            )
        except subprocess.TimeoutExpired:
            probe = None
        if probe is not None and probe.returncode == 0:
            break
        if probe is not None and ('HOST IDENTIFICATION HAS CHANGED' in probe.stderr or 'Host key verification failed' in probe.stderr):
            raise RuntimeError('SSH host key verification failed; verify the Droplet identity before updating known_hosts.')
        if time.monotonic() >= deadline:
            raise RuntimeError('SSH did not become available within 180 seconds. Check the runner IPv4 CIDR, firewall and SSH key.')
        time.sleep(5)

    encoded_program = base64.b64encode(REMOTE_PROGRAM.encode()).decode('ascii')
    command = 'python3 -c ' + shlex.quote(
        "import base64; exec(compile(base64.b64decode('" + encoded_program + "'), '<faza4-deploy>', 'exec'))"
    )
    result = subprocess.run(
        ssh + [command], input=json.dumps(configuration), text=True,
        timeout=1100, env=process_environment,
    )
    if result.returncode:
        raise RuntimeError(f'Remote deployment failed with exit code {result.returncode}.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('::error::' + str(error), file=sys.stderr)
        sys.exit(1)
