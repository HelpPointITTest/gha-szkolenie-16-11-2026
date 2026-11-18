#!/usr/bin/env python3
"""Prepare persistent paths and reuse the exact Terraform inputs of this run."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


def required(name):
    value = os.environ.get(name, '')
    if not value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f'Brak poprawnej konfiguracji {name}.')
    return value


def protected_directory(path, *, create=True):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    status = path.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) != 0o700:
        raise ValueError(f'Katalog musi należeć do użytkownika runnera i mieć prawa 0700: {path}')
    return path


def regular_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Oczekiwano zwykłego pliku: {path}')


def write_json(path, value):
    if path.is_symlink():
        raise ValueError(f'Plik nie może być dowiązaniem: {path}')
    descriptor, temporary_name = tempfile.mkstemp(prefix='.faza4-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write('\n')
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('init', 'existing'):
        raise ValueError('Użycie: prepare_job.py init|existing')
    mode = sys.argv[1]
    environment = required('TARGET_ENVIRONMENT')
    if environment not in ('dev', 'prod'):
        raise ValueError('TARGET_ENVIRONMENT musi mieć wartość dev albo prod.')
    student = required('FAZA4_STUDENT_NAME')
    if len(student) > 40 or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)+', student):
        raise ValueError('FAZA4_STUDENT_NAME: imie-nazwisko, małe litery ASCII, cyfry i myślniki; maksymalnie 40 znaków.')
    repository = required('GITHUB_REPOSITORY_ID')
    run_id = required('GITHUB_RUN_ID')
    commit = required('GITHUB_SHA')
    if not re.fullmatch(r'[1-9][0-9]*', repository) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise ValueError('Nieprawidłowy identyfikator repozytorium lub przebiegu GitHub.')
    if not re.fullmatch(r'[a-fA-F0-9]{40}', commit):
        raise ValueError('Nieprawidłowy GITHUB_SHA.')

    root = Path(required('FAZA4_STATE_ROOT'))
    if not root.is_absolute() or root == Path('/'):
        raise ValueError('FAZA4_STATE_ROOT musi być bezwzględnym, przygotowanym katalogiem stanu.')
    protected_directory(root, create=False)
    root = root.resolve()
    for name in ('GITHUB_WORKSPACE', 'RUNNER_TEMP'):
        disposable = Path(required(name)).resolve()
        if root == disposable or disposable in root.parents:
            raise ValueError(f'FAZA4_STATE_ROOT musi znajdować się poza {name}.')
    marker = root / '.faza4-state-root'
    if not marker.exists():
        raise ValueError('Brak .faza4-state-root. Przygotuj trwały katalog stanu zgodnie z instrukcją; nie twórz przypadkowo nowego state.')
    regular_file(marker)

    repository_directory = protected_directory(root / repository)
    state_directory = protected_directory(repository_directory / student)
    workspace_directory = protected_directory(state_directory / 'workspaces')
    protected_directory(workspace_directory / environment)
    initialized_directory = protected_directory(state_directory / 'initialized')
    initialized = initialized_directory / environment
    state_file = workspace_directory / environment / 'terraform.tfstate'
    if initialized.exists():
        regular_file(initialized)
        if not state_file.exists():
            raise ValueError(f'Brak wcześniej używanego state dla {environment}. Odtwórz plik z kopii; nie uruchamiaj nowego apply.')
    if state_file.exists():
        regular_file(state_file)
        state = json.loads(state_file.read_text())
        if not isinstance(state, dict) or not isinstance(state.get('version'), int):
            raise ValueError('Istniejący plik state nie jest poprawnym stanem Terraform.')
        initialized.touch(mode=0o600, exist_ok=True)

    runs_directory = protected_directory(state_directory / 'runs')
    run_directory = protected_directory(runs_directory / run_id)
    paths = {}
    for name in ('terraform-data', 'plans', 'inputs', 'releases', 'manifests'):
        paths[name] = protected_directory(run_directory / name)
    data_directory = paths['terraform-data'] / environment
    protected_directory(data_directory, create=mode == 'init')
    known_hosts_directory = protected_directory(state_directory / 'known-hosts')
    variables_file = paths['inputs'] / f'{environment}.tfvars.json'
    manifest_file = paths['manifests'] / f'{environment}.json'
    identity = {'repository_id': repository, 'student_name': student, 'run_id': run_id, 'commit': commit, 'environment': environment}

    if mode == 'init':
        cidr = required('FAZA4_SSH_ALLOWED_CIDR')
        network = ipaddress.IPv4Network(cidr, strict=True)
        if network.prefixlen != 32:
            raise ValueError('FAZA4_SSH_ALLOWED_CIDR musi wskazywać publiczny IPv4 runnera z maską /32.')
        region = os.environ.get('FAZA4_REGION') or 'fra1'
        size = os.environ.get('FAZA4_DROPLET_SIZE') or 's-1vcpu-1gb'
        if not re.fullmatch(r'[a-z]{3}[0-9]+', region) or not re.fullmatch(r'[a-z0-9][a-z0-9-]+', size):
            raise ValueError('Nieprawidłowy slug regionu lub rozmiaru Dropleta.')
        variables = {
            'environment': environment, 'student_name': student, 'region': region,
            'droplet_size': size, 'ssh_allowed_cidr': str(network),
        }
        if manifest_file.exists():
            regular_file(manifest_file)
            previous = json.loads(manifest_file.read_text())
            if any(previous.get(name) != value for name, value in identity.items()) or previous.get('variables') != variables:
                raise ValueError('Konfiguracja tego przebiegu została zmieniona. Rozpocznij nowy przebieg workflow.')
        write_json(variables_file, variables)
        manifest = {**identity, 'variables': variables, 'tfvars_sha256': hashlib.sha256(variables_file.read_bytes()).hexdigest()}
        write_json(manifest_file, manifest)
    else:
        regular_file(variables_file)
        regular_file(manifest_file)
        manifest = json.loads(manifest_file.read_text())
        if any(manifest.get(name) != value for name, value in identity.items()):
            raise ValueError('Metadane wejściowe nie odpowiadają bieżącemu przebiegowi i środowisku.')
        if manifest.get('tfvars_sha256') != hashlib.sha256(variables_file.read_bytes()).hexdigest():
            raise ValueError('Plik tfvars został zmieniony od etapu init.')

    values = {
        'STATE_DIRECTORY': state_directory,
        'TF_DATA_DIR': data_directory,
        'TF_PLAN_FILE': paths['plans'] / f'{environment}.tfplan',
        'TF_VARS_FILE': variables_file,
        'TF_STATE_FILE': state_file,
        'SSH_KNOWN_HOSTS_FILE': known_hosts_directory / environment,
        'DEPLOY_METADATA_FILE': paths['releases'] / f'{environment}.json',
    }
    with open(required('GITHUB_ENV'), 'a') as destination:
        for name, value in values.items():
            destination.write(f'{name}={value}\n')
    print(f'Przygotowano trwałe ścieżki i wejścia Terraform: {student}, {environment}, tryb {mode}.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print('::error::' + str(error), file=sys.stderr)
        sys.exit(1)
