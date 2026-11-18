#!/usr/bin/env python3
"""Validate environment publication metadata and Terraform outputs, without secrets."""

import ipaddress
import json
import os
from pathlib import Path
import re
import sys


def main():
    environment = os.environ['TARGET_ENVIRONMENT']
    if environment not in ('dev', 'prod'):
        raise ValueError('Nieprawidłowe środowisko DigitalOcean.')
    release = json.loads(Path('release-metadata/release.json').read_text())
    tag = os.environ['IMAGE_TAG']
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,86}', tag):
        raise ValueError('Nieprawidłowy FAZA4_IMAGE_TAG.')
    expected = {
        'environment': environment, 'image_tag': tag,
        'commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
    }
    if any(release.get(key) != value for key, value in expected.items()):
        raise ValueError('Artefakt publikacji nie odpowiada środowisku lub przebiegowi.')
    digest = release.get('image_digest')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('Brak prawidłowego digestu opublikowanego obrazu.')
    outputs = json.loads(Path(os.environ['DEPLOY_METADATA_FILE']).read_text())
    address = str(ipaddress.IPv4Address(outputs['public_ip']['value']))
    if not ipaddress.IPv4Address(address).is_global:
        raise ValueError('Terraform nie zwrócił publicznego IPv4.')
    droplet_id = str(outputs['droplet_id']['value'])
    if not re.fullmatch(r'[1-9][0-9]*', droplet_id):
        raise ValueError('Terraform nie zwrócił prawidłowego ID Dropleta.')
    app_url = 'http://' + address
    if outputs['app_url']['value'] != app_url:
        raise ValueError('URL aplikacji nie odpowiada IP z Terraform.')
    city = os.environ.get('WEATHER_CITY', '')
    if not city.strip() or len(city) > 100 or any(ord(char) < 32 for char in city):
        raise ValueError('FAZA4_WEATHER_CITY musi zawierać nazwę miasta w jednej linii.')
    values = {
        'DEPLOY_HOST': address, 'DROPLET_ID': droplet_id,
        'APP_URL': app_url, 'IMAGE_REFERENCE': os.environ['HUB_IMAGE_BASE'] + '@' + digest,
    }
    with open(os.environ['GITHUB_ENV'], 'a') as destination:
        for name, value in values.items():
            destination.write(f'{name}={value}\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print('::error::Nieprawidłowe metadane publikacji lub wynik Terraform; przerwano deploy.', file=sys.stderr)
        sys.exit(1)
