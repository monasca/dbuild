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

import datetime
import logging
import os
import re

from collections import deque

import docker

from docker.errors import BuildError

from dbuild.docker_utils import (ARG_BUILD_ARG, ARG_VARIANT,
                                 ARG_REBUILD, ARG_TAG, ARG_APPEND,
                                 load_config, resolve_variants,
                                 get_variant, verify_docker_version,
                                 load_dockerfile, get_rebuild_targets)
from dbuild.verb import verb, VerbException, Plan

REGEX_DOCKER_BUILD_STEP = re.compile(r'^Step (\d+)/(\d+) : ([A-Z]+)')
REGEX_DOCKER_BUILD_SUCCESS = re.compile(r'^(Successfully built |sha256:)([0-9a-f]+)')


logger = logging.getLogger(__name__)


ARG_TYPES = [
    ARG_TAG,
    ARG_BUILD_ARG,
    ARG_VARIANT,
    ARG_REBUILD,
    ARG_APPEND
]


def get_proxy_config():
    proxies = {}

    if 'HTTP_PROXY' in os.environ:
        proxies['HTTP_PROXY'] = os.environ['HTTP_PROXY']
    elif 'http_proxy' in os.environ:
        proxies['HTTP_PROXY'] = os.environ['http_proxy']

    if 'HTTPS_PROXY' in os.environ:
        proxies['HTTPS_PROXY'] = os.environ['HTTPS_PROXY']
    elif 'https_proxy' in os.environ:
        proxies['HTTPS_PROXY'] = os.environ['https_proxy']

    if 'NO_PROXY' in os.environ:
        proxies['NO_PROXY'] = os.environ['NO_PROXY']
    elif 'NO_PROXY' in os.environ:
        proxies['NO_PROXY'] = os.environ['no_proxy']

    # copy UPPER to lower for badly behaved apps
    if 'HTTP_PROXY' in proxies:
        proxies['http_proxy'] = proxies['HTTP_PROXY']
    if 'HTTPS_PROXY' in proxies:
        proxies['https_proxy'] = proxies['HTTPS_PROXY']
    if 'NO_PROXY' in proxies:
        proxies['no_proxy'] = proxies['NO_PROXY']

    return proxies


def execute_plan(plan):
    plan.status.blocking = False

    module_path = os.path.join(plan.arguments['base_path'], plan.module)
    images = [tag.full_interp for tag in plan.arguments['tags']]

    first_image = images.pop(0)
    plan.status.description = 'build %s' % first_image

    client = docker.from_env(version='auto')

    logger.debug('building: path=%s, tag=%s, args=%r',
                 module_path, first_image, plan.arguments['build_args'])

    build_log = plan.arguments['build_log']
    if plan.arguments['log_file']:
        log_file = open(plan.arguments['log_file'], 'w')
    else:
        log_file = None

    # build phase
    stream = client.api.build(buildargs=plan.arguments['build_args'],
                              path=module_path, rm=True, tag=first_image,
                              decode=True)
    last_events = deque(maxlen=2)
    for event in stream:
        last_events.append(event)

        if 'error' in event:
            logger.error(event['error'])
            plan.status.description = 'error'

        if 'stream' in event:
            m = REGEX_DOCKER_BUILD_STEP.match(event['stream'])

            for line in event['stream'].strip().splitlines():
                if build_log:
                    logger.info('build %s: %s', plan.module, line)

                if log_file:
                    log_file.write(line)
                    log_file.write('\n')
            if m:
                step = m.group(1)
                plan.status.current = int(step)

                start, end = m.span()
                cmd_snippet = event['stream'][end:20].strip()
                plan.status.description = 'build %s %s %s' % (first_image,
                                                              m.group(3),
                                                              cmd_snippet)

    if log_file:
        log_file.close()

    # grabbed from docker-py/docker/models/images.py:ImageCollection.build
    if not last_events[-1]:
        raise BuildError('Unknown')

    # the last line must say success, otherwise the build failed
    image_id = None
    build_errors = []
    for event in last_events:
        m = REGEX_DOCKER_BUILD_SUCCESS.match(event.get('stream') or '')
        if event.get('error'):
            build_errors.append(event.get('error'))

        if m:
            image_id = m.group(2)

    if build_errors:
        raise BuildError(build_errors)

    if not image_id:
        if build_errors:
            raise BuildError('Build failed, errors: %r' % build_errors)
        else:
            raise BuildError('Build did not succeed. Last '
                             'line: %s' % last_events[-1])

    image = client.images.get(image_id)

    plan.artifacts.append(first_image)

    # tagging phase
    plan.status.current = plan.status.total
    for extra_tag in images:
        repo, tag = extra_tag.rsplit(':', 1)
        image.tag(repo, tag=tag)

        plan.artifacts.append(extra_tag)


@verb('build', priority=1, args=ARG_TYPES,
      description='builds specified modules')
def build(global_args, verb_args, module, intents):
    verify_docker_version()

    base_config = load_config(global_args.base_path, module)
    dockerfile = load_dockerfile(global_args.base_path, module)

    build_args = get_proxy_config()
    if 'args' in base_config:
        build_args.update(base_config['args'])

    override_tags = []
    rebuild_targets = []

    for arg in filter(lambda a: a.type == 'build_arg', verb_args):
        k, v = arg.groups
        build_args[k] = v

    for arg in filter(lambda a: a.type == 'rebuild', verb_args):
        rebuild_targets.append(arg.groups[0])

    for arg in filter(lambda a: a.type == 'image_override_tag', verb_args):
        override_tags.append(arg.groups[0])

    logger.debug('Resolved build parameters:')
    logger.debug('build_args: %r', build_args)
    logger.debug('rebuild_targets: %r', rebuild_targets)

    if rebuild_targets:
        valid_targets = get_rebuild_targets(dockerfile)
        if not valid_targets:
            logger.error('Module has no rebuild targets, invalid: %s',
                         rebuild_targets)
            raise VerbException()

        for target in rebuild_targets:
            if target.lower() not in valid_targets:
                logger.error('Invalid rebuild target %s, must be one of: %s',
                             target, ', '.join(valid_targets))
                raise VerbException()

    rebuild_str = datetime.datetime.now().isoformat()
    for target in rebuild_targets:
        build_args['REBUILD_%s' % target.upper()] = rebuild_str

    variants = resolve_variants(verb_args, base_config)
    logger.debug('Resolved variants: %r', variants)

    plans = []
    for variant_args in variants:
        variant = get_variant(base_config, variant_args['variant_tag'])

        if variant and 'args' in variant:
            variant_build_args = variant['args'].copy()
        else:
            variant_build_args = {}
        variant_build_args.update(build_args)
        logger.debug('variant_build_args: %r', variant_build_args)

        # we'll generate a set of images for tasks later in the pipeline, e.g.
        # push - not used for build
        images = set()
        for tag in variant_args['tags']:
            images.add(tag.full)

        variant_intents = intents.copy()
        if 'images' in variant_intents:
            variant_intents['images'].update(images)
        else:
            variant_intents['images'] = images

        if global_args.build_log_dir:
            datestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            file_name = '%s-%s-%s.log' % (datestamp, module,
                                          variant_args['variant_tag'])
            log_file = os.path.join(global_args.build_log_dir, file_name)
        else:
            log_file = None

        plan = Plan('build', module, execute_plan, variant_intents, {
            'base_path': global_args.base_path,
            'tags': variant_args['tags'],
            'build_args': variant_build_args,
            'build_log': global_args.build_log,
            'log_file': log_file
        })
        plan.status.total = len(dockerfile.structure)
        plans.append(plan)

    return plans
