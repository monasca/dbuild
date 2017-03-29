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

import logging

import docker

from dbuild.docker_utils import (ARG_VARIANT, ARG_APPEND, ARG_TAG,
                                 load_config, resolve_variants)
from dbuild.verb import verb, Plan

logger = logging.getLogger(__name__)


ARG_TYPES = [ARG_VARIANT, ARG_APPEND, ARG_TAG]


def execute_plan(plan):
    client = docker.from_env(version='auto')

    image = plan.arguments['image']
    plan.status.description = 'push %s' % image

    repo, tag = image.rsplit(':', 1)
    client.images.push(repo, tag=tag)


def images_from_args(global_args, verb_args, module):
    base_config = load_config(global_args.base_path, module)

    variants = resolve_variants(verb_args, base_config)
    logger.debug('Resolved variants: %r', variants)

    images = set()
    for variant in variants:
        for tag in variant['tags']:
            images.add(tag.full)

    return images


@verb('push', args=ARG_TYPES, description='pushes specified modules')
def push(global_args, verb_args, module, intents):
    if 'images' in intents:
        logger.debug('Pushing collected images from build intents')
        images = intents['images']
    else:
        logger.debug('Pushing collected images from user args')
        images = images_from_args(global_args, verb_args, module)

    plans = []
    for image in images:
        plans.append(Plan('push', module, execute_plan, intents,
                          {'image': image}))

    return plans

