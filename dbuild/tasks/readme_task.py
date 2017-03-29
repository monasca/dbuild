# (C) Copyright 2017 Hewlett Packard Enterprise Development LP
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import getpass
import logging
import os

import requests

from dbuild.docker_utils import (ARG_TAG,
                                 load_config, resolve_variants, get_variant)
from dbuild.verb import verb, Plan, VerbException

logger = logging.getLogger(__name__)

DOCKER_HUB_API = os.environ.get('DOCKER_HUB_API', 'https://hub.docker.com')
DOCKER_HUB_ENDPOINT_LOGIN = '/v2/users/login/'
DOCKER_HUB_ENDPOINT_REPOSITORIES = '/v2/repositories/'
DOCKER_HUB_USERNAME = os.environ.get('DOCKER_HUB_USERNAME', None)
DOCKER_HUB_PASSWORD = os.environ.get('DOCKER_HUB_PASSWORD', None)
DOCKER_HUB_TOKEN = os.environ.get('DOCKER_HUB_TOKEN', None)

_auth_token = None
ca = '/home/tim/.config/betwixt/ssl/certs/ca.pem'


def get_auth_token():
    global _auth_token

    if _auth_token:
        return _auth_token

    if DOCKER_HUB_TOKEN:
        return DOCKER_HUB_TOKEN

    username = DOCKER_HUB_PASSWORD
    if not username:
        username = raw_input('Docker Hub username: ')

    password = DOCKER_HUB_PASSWORD
    if not password:
        password = getpass.getpass('Docker Hub password: ')

    r = requests.post(DOCKER_HUB_API + DOCKER_HUB_ENDPOINT_LOGIN, json={
        'username': username,
        'password': password
    }, verify=ca)
    r.raise_for_status()

    _auth_token = r.json()['token']
    return _auth_token


def execute_plan(plan):
    if plan.arguments.get('skip', False):
        logger.debug('skipping readme update for %s', plan.module)
        return

    tag = plan.arguments['tag']
    headers = {'Authorization': 'JWT %s' % plan.arguments['token']}
    with open(plan.arguments['readme_path'], 'r') as f:
        readme = f.read()

        url = '%s%s%s/' % (DOCKER_HUB_API,
                           DOCKER_HUB_ENDPOINT_REPOSITORIES,
                           tag.repository)
        r = requests.patch(url, headers=headers, json={
            'full_description': readme
        }, verify=ca)
        if r.status_code == requests.codes.ok:
            logger.info('Updated README for repository: %s', tag.repository)
            logger.info(r.content)
        else:
            logger.warn('Failed to update README for repository %s:', tag.repository)
            logger.warn('Server said: %s', r.text)


@verb('readme', args=[ARG_TAG], description='updates a DockerHub readme')
def readme(global_args, verb_args, module, intents):
    readme_path = os.path.join(global_args.base_path, module, 'README.md')
    if not os.path.exists(readme_path):
        logger.info('no README.md exists for module %s, will not update', module)
        return [Plan('readme', module, execute_plan, intents, {
            'skip': True
        })]

    token = get_auth_token()
    if not token:
        logger.warn('could not authenticate to docker hub, '
                    'skipping README update')
        return [Plan('readme', module, execute_plan, intents, {
            'skip': True
        })]

    base_config = load_config(global_args.base_path, module)
    variants = resolve_variants(verb_args, base_config, check_tag=False)

    known = set()
    plans = []
    for variant in variants:
        for tag in variant['tags']:
            if tag.repository in known:
                continue

            if tag.registry is not None:
                logger.debug('Cannot update README for private registries, '
                             'will skip: %r', tag)
                continue

            plans.append(Plan('readme', module, execute_plan, intents, {
                'token': get_auth_token(),
                'variant_tag': variant['variant_tag'],
                'tag': tag,
                'readme_path': readme_path
            }))

    if not plans:
        logger.debug('no READMEs can be updated, skipping...')
        return [Plan('readme', module, execute_plan, intents, {
            'skip': True
        })]

    return plans
