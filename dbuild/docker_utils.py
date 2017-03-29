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

import glob
import logging
import os
import subprocess
import re

import yaml

from distutils.version import LooseVersion
from dockerfile_parse import DockerfileParser

from dbuild.tag import (TAG_REGEXES, DockerTag,
                        parse_docker_tag, docker_tags_from_args)
from dbuild.verb import Argument, VerbException

REGEX_MODULE = re.compile(r'^[a-z0-9\-]+$')
REGEX_DOCKERFILE_REBUILD = re.compile(r'^REBUILD_([A-Z_]+)=.+$')

ARG_TAG = Argument('tag', TAG_REGEXES)

ARG_BUILD_ARG = Argument('build_arg', re.compile(r'^([\w_]+)=(.*)$'))
ARG_VARIANT = Argument('variant', re.compile(r'^(\w[\w_.-]*)$'))
ARG_REBUILD = Argument('rebuild', re.compile(r'^@(\w[\w_.-]*)$'))
ARG_APPEND = Argument('append', re.compile(r'^\+$'))

MIN_DOCKER_VERSION = LooseVersion('1.13.0')

logger = logging.getLogger(__name__)
config_cache = {}
dockerfile_cache = {}


def load_config(base_path, module):
    conf_path = os.path.join(base_path, module, 'build.yml')
    if conf_path in config_cache:
        return config_cache[conf_path]

    if not os.path.exists(conf_path):
        return {}

    with open(conf_path, 'r') as f:
        ret = yaml.safe_load(f)
        config_cache[conf_path] = ret
        return ret


def get_canonical_variants(config, variants):
    canonical_variants = set()
    for variant in variants:
        if variant == 'all':
            canonical_variants.update(map(lambda v: v['tag'],
                                          config['variants']))
            continue

        found = False
        for defined_variant in config['variants']:
            if variant == defined_variant['tag']:
                canonical_variants.add(variant)
                found = True
            elif variant in defined_variant['aliases']:
                canonical_variants.add(defined_variant['tag'])
                found = True

        if not found:
            # TODO maybe just ignore this?
            # then many variants could be built across modules that might not
            # share names
            logger.error('Variant not found in build.yml for %s: %s. '
                         'Note that the same variants must exist in all '
                         'modules.', config['repository'], variant)
            raise VerbException()

    return canonical_variants


def get_variant(config, variant_tag):
    if 'variants' not in config:
        return {}

    for variant in config['variants']:
        if variant['tag'] == variant_tag:
            return variant

    return None


def resolve_variants(verb_args, config, check_tag=True):
    base_tag = DockerTag().mutate(repository=config.get('repository', None))

    variants = []
    for arg in filter(lambda a: a.type == 'variant', verb_args):
        variants.append(arg.groups[0])

    if variants and 'variants' not in config:
        logger.error('No variants defined in build.yml!')
        raise VerbException()

    tag_args = filter(lambda a: a.type == 'tag', verb_args)
    append = filter(lambda a: a.type == 'append', verb_args)

    canonical_variants = get_canonical_variants(config, variants)
    if canonical_variants:
        resolved_variants = []
        for variant_tag in canonical_variants:
            variant = get_variant(config, variant_tag)

            variant_base_tag = base_tag.mutate(tag=variant_tag)
            if 'repository' in variant:
                variant_base_tag = variant_base_tag.mutate(
                    repository=variant['repository'])

            tags = []
            if variant_base_tag.is_complete():
                tags.append(variant_base_tag)

            if 'aliases' in variant:
                tags.extend(docker_tags_from_args(variant['aliases'],
                                                  variant_base_tag))

            if tag_args:
                str_args = [t.value for t in tag_args]
                dtags = docker_tags_from_args(str_args, variant_base_tag,
                                              check_tag)
                if append:
                    tags.extend(dtags)
                else:
                    tags = dtags

            if not tags:
                raise VerbException(
                    'At least one complete tag is required. '
                    'Base: %r, known: %r' % (variant_base_tag, tags))

            resolved_variants.append({
                'variant_tag': variant_tag,
                'tags': tags
            })

        return resolved_variants
    else:
        logger.info('No variant given, using inferred base tag: %r', base_tag)
        str_args = [t.value for t in tag_args]
        tags = docker_tags_from_args(str_args, base_tag, check_tag)
        if not tags:
            raise VerbException('No valid Docker tag given in arguments, '
                                'known: %r', tags)

        return [{
            'variant_tag': None,
            'tags': tags
        }]


def load_dockerfile(base_path, module):
    dockerfile_path = os.path.join(base_path, module, 'Dockerfile')
    if dockerfile_path in dockerfile_cache:
        return dockerfile_cache[dockerfile_path]

    with open(dockerfile_path, 'r') as f:
        p = DockerfileParser()
        p.content = f.read()
        dockerfile_cache[dockerfile_path] = p
        return p


def get_rebuild_targets(dockerfile):
    targets = []
    for ins in dockerfile.structure:
        if ins['instruction'] != 'ARG':
            continue

        m = REGEX_DOCKERFILE_REBUILD.match(ins['value'])
        if m:
            targets.append(m.group(1).lower())

    return targets


def list_modules(path):
    all_modules = map(lambda p: os.path.basename(os.path.dirname(p)),
                      glob.glob(os.path.join(path, '*/Dockerfile')))

    valid_modules = []
    for module in all_modules:
        m = REGEX_MODULE.match(module)
        if m:
            valid_modules.append(module)
        else:
            logging.debug('Ignoring module with invalid name: %s', module)

    return valid_modules


class SubprocessException(Exception):
    def __init__(self, retcode, stdout, stderr):
        super(SubprocessException, self).__init__(stderr)

        self.retcode = retcode
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return 'CaptureException(retcode=%s, stdout=%r, stderr=%r)' % (
            self.retcode,
            self.stdout,
            self.stderr
        )


def exec_docker(args):
    logger.debug('Running docker: %r', args)
    p = subprocess.Popen(['docker'] + args)

    ret = p.wait()
    if p.returncode != 0:
        raise SubprocessException(ret, None, None)


def capture_docker(args):
    logger.debug('Capturing docker: %r', args)
    p = subprocess.Popen(['docker'] + args,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

    out, err = p.communicate()
    if p.returncode != 0:
        raise SubprocessException(p.returncode, out, err)

    return out, err


class InvalidDockerVersionException(Exception):
    pass


def get_docker_client_version():
    out, err = capture_docker(['version', '-f', '{{.Client.Version}}'])
    return LooseVersion(out.strip())


def verify_docker_version():
    docker_client_version = get_docker_client_version()
    if docker_client_version >= MIN_DOCKER_VERSION:
        logger.debug('Docker version %s meets requirement >= %s',
                     docker_client_version, MIN_DOCKER_VERSION)
    else:
        raise InvalidDockerVersionException(
            'Installed Docker version %s does not meet requirement >= %s' % (
                docker_client_version, MIN_DOCKER_VERSION
            ))
