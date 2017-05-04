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

from functools import wraps

import attr

logger = logging.getLogger(__name__)

verbs = {}


@attr.s
class VerbDefinition(object):
    name = attr.ib()
    aliases = attr.ib()
    function = attr.ib()
    description = attr.ib()
    priority = attr.ib()
    args = attr.ib()


@attr.s
class Argument(object):
    type = attr.ib()
    regex = attr.ib(repr=False)


@attr.s
class Value(object):
    type = attr.ib()
    value = attr.ib()
    groups = attr.ib()


_count = 0


def inc_count():
    global _count
    _count += 1
    return _count


@attr.s
class ExecutionStatus(object):
    current = attr.ib(default=0)
    total = attr.ib(default=1)
    description = attr.ib(default=None)

    started = attr.ib(default=False)
    finished = attr.ib(default=False)
    failed = attr.ib(default=False)
    cancelled = attr.ib(default=False)
    cancel_requested = attr.ib(default=False)
    future = attr.ib(default=None)
    blocking = attr.ib(default=True)

    @property
    def success(self):
        return self.finished and not (self.failed or self.cancelled)

    @property
    def as_str(self):
        if self.success:
            return 'success'
        elif self.failed:
            return 'failed'
        elif self.cancelled:
            return 'cancelled'
        else:
            return 'other'


@attr.s
class Plan(object):
    verb = attr.ib()
    module = attr.ib()
    function = attr.ib(repr=False)
    intents = attr.ib(repr=False)
    arguments = attr.ib(repr=False)

    id = attr.ib(default=attr.Factory(inc_count))

    parent = attr.ib(default=None, repr=False)
    children = attr.ib(default=attr.Factory(list), repr=False)
    status = attr.ib(default=attr.Factory(ExecutionStatus), repr=False)

    artifacts = attr.ib(default=attr.Factory(list), repr=False)

    def is_dead(self):
        if self.parent and self.parent.status.failed:
            return True

        if self.status.cancelled:
            return True

        return False

    def is_ready(self):
        if not self.parent:
            return True

        if not self.parent.status.finished:
            return False

        for sibling in self.parent.children:
            if sibling is self:
                continue

            if sibling.status.started and \
                    sibling.status.blocking and \
                    not sibling.status.finished:
                return False

        return True

    def active_in_tree(self):
        active = []

        # active = actually executing right now
        if self.status.started and not self.status.finished:
            active.append(self)

        for child in self.children:
            active.extend(child.active_in_tree())

        return active

    @property
    def steps(self):
        return 1 + sum(map(lambda c: c.steps, self.children))

    @property
    def current_progress(self):
        children = sum(c.current_progress for c in self.children)
        return self.status.current + children

    @property
    def total_progress(self):
        children = sum(c.total_progress for c in self.children)
        return self.status.total + children


def verb(*names, **kwargs):
    global verbs

    if len(names) == 0:
        raise ValueError('At least one verb name is required')

    def verb_decorator(func):
        verb_def = VerbDefinition(
            name=names[0],
            aliases=names[1:],
            function=func,
            description=kwargs.get('description', None),
            priority=kwargs.get('priority', 0),
            args=kwargs.get('args', []))

        for verb_name in names:
            verbs[verb_name] = verb_def

        @wraps(func)
        def func_wrapper(*args, **kwargs_):
            return func(*args, **kwargs_)

        return func_wrapper

    return verb_decorator


class VerbException(Exception):
    pass


class UnhandledArgumentException(VerbException):
    pass


def verb_arguments(args, verb_subset=None):
    global verbs

    consumed_args = set()
    verb_subset = verb_subset if verb_subset is not None else verbs.keys()

    arg_dict = {}  # { verb_name: [arg_val, ...] }
    for verb_name in verb_subset:
        verb_def = verbs[verb_name]

        verb_args = []
        for arg_def in verb_def.args:
            for arg in args:
                if isinstance(arg_def.regex, list):
                    regexes = arg_def.regex
                else:
                    regexes = [arg_def.regex]

                for regex in regexes:
                    m = regex.match(arg)
                    if m:
                        verb_args.append(Value(arg_def.type, arg, m.groups()))
                        consumed_args.add(arg)
                        break

        arg_dict[verb_name] = verb_args

    remaining = set(args) - consumed_args
    if remaining:
        logger.error('Not all arguments were handled by a verb!')
        logger.error('Make sure the following arguments are correct:')
        for arg in remaining:
            logger.error(' - %s', arg)
        raise UnhandledArgumentException(repr(list(remaining)))

    return arg_dict
