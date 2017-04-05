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

import re

import attr

# match only a registry, e.g. repo.example.com:1234
RE_REGISTRY = re.compile(r'^([\w.-]+:[\d]+)$')

# match only a namespace, e.g. someuser/
RE_NAMESPACE = re.compile(r'^(\w[\w.-]*)/$')

# match only an image, e.g. /someimage
RE_IMAGE = re.compile(r'^/(\w[\w_.-]*)$')

# match a repository (namespace + image, no tag), e.g. someuser/someimage
RE_REPOSITORY = re.compile(r'^(\w[\w.-]*)/(\w[\w.-]*)$')

# match only a tag, e.g. :tag
RE_TAG = re.compile(r'^:(\w[\w_.-]*)$')

# match an image with a tag, e.g. someimage:tag
RE_TAGGED_IMAGE = re.compile(r'^/(\w[\w_.-]*):(\w[\w_.-]*)$')

# match an untagged registry and namespace e.g. repo.example.com:1234/repo
RE_REGISTRY_NAMESPACE = re.compile(r'^([\w.-]+:[\d]+)/(\w[\w.-]*)$')

# match an untagged registry and repository e.g. repo.example.com:1234/repo/image
RE_REGISTRY_REPOSITORY = re.compile(r'^([\w.-]+:[\d]+)/(\w[\w.-]*)/(\w[\w.-]*)$')

# match a full tag expression, e.g. hub.registry.com:1234/repo/image:tag
RE_FULL = re.compile(r'^([\w.-]+(?::[\d]+)?)/(\w[\w.-]*)/(\w[\w.-]*):(\w[\w_.-]*)$')

# match a full tag w/ implicit registry (docker hub), e.g. repo/image:tag
RE_FULL_IMPLICIT = re.compile(r'^(\w[\w.-]*)/(\w[\w.-]*):(\w[\w_.-]*)$')

TAG_REGEXES = [RE_REGISTRY, RE_NAMESPACE, RE_IMAGE, RE_REPOSITORY,
               RE_TAG, RE_TAGGED_IMAGE, RE_REGISTRY_NAMESPACE,
               RE_REGISTRY_REPOSITORY, RE_FULL, RE_FULL_IMPLICIT]


@attr.s
class DockerTag(object):
    registry = attr.ib(default=None)
    namespace = attr.ib(default=None)
    image = attr.ib(default=None)
    tag = attr.ib(default=None)

    @property
    def repository(self):
        # our terminology is a bit different than docker's own here as we
        # split a 'repository' into its separate (some optional) components
        parts = []
        if self.registry:
            r = self.registry
            if ':' in r:
                host, port = r.split(':')
                if port == '443':
                    r = host

            parts.append(r)

        if self.namespace:
            parts.append(self.namespace)

        parts.append(self.image)

        return '/'.join(parts)

    @property
    def full(self):
        if self.tag:
            return '%s:%s' % (self.repository, self.tag)
        else:
            return self.repository

    def mutate(self, **kwargs):
        registry, namespace, image = None, None, None
        if 'repository' in kwargs:
            if '/' in kwargs['repository']:
                namespace, image = kwargs['repository'].rsplit('/', 1)
                if '/' in namespace:
                    registry, namespace = namespace.split('/', 1)
            else:
                image = kwargs['repository']

        namespace = kwargs.get('namespace', namespace)
        image = kwargs.get('image', image)

        return DockerTag(
            registry=kwargs.get('registry', None) or self.registry,
            namespace=namespace or self.namespace,
            image=image or self.image,
            tag=kwargs.get('tag', None) or self.tag)

    def merge(self, other):
        return DockerTag(
            registry=other.registry or self.registry,
            namespace=other.namespace or self.namespace,
            image=other.image or self.image,
            tag=other.tag or self.tag)

    def is_complete(self, check_tag=True):
        """True when enough is known that `docker push` can be run
        successfully"""

        # need a tag and either:
        #  - a namespace and an image, or
        #  - a registry and an image
        # (assuming we can't push to implicit docker hub library namespace)
        # docker allows an implicit 'latest' tag, but we'll be more strict for
        # simplicity
        if check_tag and not self.tag:
            return False

        if self.namespace and self.image:
            return True

        if self.registry and self.image:
            return True

        return False


def parse_docker_tag(text):
    registry = None
    namespace = None
    image = None
    tag = None

    sections = text.split('/')
    if len(sections) == 3:
        # registry.example.com:1234/namespace/image:tag
        registry = sections[0]
        namespace = sections[1]
        image = sections[2]
    elif len(sections) == 2:
        if ':' in sections[0]:
            # registry.example.com:1234/image:tag
            registry = sections[0]
            image = sections[1]
        else:
            # namespace/image:tag
            namespace = sections[0]
            image = sections[1]
    elif len(sections) == 1:
        image = sections[0]

    if ':' in image:
        image, tag = image.split(':', 1)

    return DockerTag(registry, namespace, image, tag)


class DockerTagParseException(Exception):
    pass


def mutate_tag(base_tag, arg):
    m = RE_FULL.match(arg)
    if m:
        return DockerTag(*m.groups())

    m = RE_FULL_IMPLICIT.match(arg)
    if m:
        namespace, image, tag = m.groups()
        return DockerTag(None, namespace, image, tag)

    m = RE_REGISTRY.match(arg)
    if m:
        return base_tag.mutate(registry=m.group(1))

    m = RE_NAMESPACE.match(arg)
    if m:
        return base_tag.mutate(namespace=m.group(1))

    m = RE_IMAGE.match(arg)
    if m:
        return base_tag.mutate(image=m.group(1))

    m = RE_REPOSITORY.match(arg)
    if m:
        namespace, image = m.groups()
        return base_tag.mutate(namespace=namespace, image=image)

    m = RE_TAG.match(arg)
    if m:
        return base_tag.mutate(tag=m.group(1))

    m = RE_TAGGED_IMAGE.match(arg)
    if m:
        image, tag = m.groups()
        return base_tag.mutate(image=image, tag=tag)

    m = RE_REGISTRY_NAMESPACE.match(arg)
    if m:
        registry, namespace = m.groups()
        return base_tag.mutate(registry=registry, namespace=namespace)

    m = RE_REGISTRY_REPOSITORY.match(arg)
    if m:
        registry, namespace, image = m.groups()
        return base_tag.mutate(registry=registry, namespace=namespace, image=image)

    raise DockerTagParseException('Invalid argument: %r' % arg)


def docker_tags_from_args(args, base_tag=None, check_tag=True):
    """Given a list of tag arguments, generate a list of DockerTags

    Each arg represents one in a sequence of mutations to the (possibly empty)
    base_tag. Whenever the mutation would result in a complete tag (as per
    DockerTag.is_complete()), it is added to the list of tags to return.

    Given a complete base_tag, all possible mutations will result in a new,
    complete tag. In most situations, this means there should be one returned
    DockerTag for each entry in `args`.

    If arguments are provided and but no complete tags can be generated,
    a DockerTagParseException will be raised. An empty list of arguments
    will result in an empty list of tags.

    :param args: an ordered list of tag arguments to apply
    :param base_tag: a DockerTag with initial fields set
    :param check_tag: if true, require complete DockerTags to have a set `tag`
                      field
    :return: a list of DockerTags
    """
    tags = []

    current = DockerTag()
    if base_tag:
        current = current.merge(base_tag)

    for arg in args:
        current = mutate_tag(current, arg)

        if current.is_complete(check_tag):
            tags.append(current)

    if args and not tags:
        raise DockerTagParseException('Tag arguments were provided, but no '
                                      'complete tag could be determined. '
                                      'More information must be specified in '
                                      'args: %r', args)

    return tags
