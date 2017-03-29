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

from dbuild.docker_utils import (ARG_VARIANT, ARG_APPEND, ARG_TAG,
                                 load_config, resolve_variants)
from dbuild.verb import verb

logger = logging.getLogger(__name__)

ARG_TYPES = [ARG_VARIANT, ARG_APPEND, ARG_TAG]


@verb('resolve', args=ARG_TYPES,
      description='tests variant resolver against args')
def resolve(global_args, verb_args, module, intents):
    base_config = load_config(global_args.base_path, module)
    variants = resolve_variants(verb_args, base_config)

    print 'resolved tags:', module
    for variant in variants:
        print '  %s' % variant['variant_tag']
        for tag in variant['tags']:
            print '    %s' % tag.full
    print ''
