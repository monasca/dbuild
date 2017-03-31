#!/usr/bin/env python

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

import importlib
import logging
import os
import signal
import sys
import textwrap
import time

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

from tqdm import tqdm

from dbuild.docker_utils import list_modules
from dbuild.verb import verbs, verb_arguments, VerbException

WORKER_STATUS_POLL_WAIT = 0.5

stream_handler = logging.StreamHandler(stream=sys.stderr)
stream_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
logging.root.addHandler(stream_handler)
logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

base_path = os.path.realpath(os.getcwd())

_cancelled = False
_cancelled_ack = False
_killed = False
_killed_ack = False


def load_verbs():
    importlib.import_module('dbuild.tasks.build_task')
    importlib.import_module('dbuild.tasks.push_task')
    importlib.import_module('dbuild.tasks.info_task')
    importlib.import_module('dbuild.tasks.resolve_task')
    importlib.import_module('dbuild.tasks.readme_task')


def build_plan_tree(global_args, verb_args, module, verb_defs, intents=None):
    if intents is None:
        intents = {}

    plans = []

    verb_def = verb_defs[0]
    try:
        ret = verb_def.function(global_args,
                                verb_args[verb_def.name],
                                module, intents)
        if not ret:
            # no plans from this verb, move on
            return []

        # recurse on remaining verbs
        remaining_verbs = verb_defs[1:]
        for plan in ret:
            plans.append(plan)
            if remaining_verbs:
                plan.children = build_plan_tree(global_args, verb_args,
                                                module, remaining_verbs,
                                                plan.intents)
                for child in plan.children:
                    child.parent = plan
    except VerbException as ex:
        logger.error('Error while building execution plan, exiting!')
        logger.error('Reason: %s', ex)
        logger.debug(ex, exc_info=True)
        logger.debug('last verb: %r', verb_def)
        logger.debug('global_args=%r verb_args=%r module=%r',
                     global_args, verb_args, module)
        sys.exit(1)

    return plans


def print_plans(plans, offset=''):
    for plan in plans:
        print textwrap.fill(repr(plan),
                            width=200,
                            initial_indent=offset,
                            subsequent_indent=offset + '     ')

        print_plans(plan.children, offset + '  ')


def flatten(dest, plans):
    next_level = []
    for plan in plans:
        dest.append(plan)

        next_level.extend(plan.children)

    if next_level:
        flatten(dest, next_level)

    return dest


# noinspection PyBroadException
def execute_single_plan(plan):
    if plan.is_dead():
        plan.status.failed = True
        plan.status.finished = True
        return plan

    try:
        plan.function(plan)
    except Exception:
        logger.exception('Exception while executing plan: %r', plan)
        plan.status.failed = True

    plan.status.finished = True
    plan.status.current = plan.status.total
    plan.status.description = None

    return plan


def submission_thread_func(flat_plans, workers):
    flat_plans = flat_plans[:]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        submitted = 0
        while flat_plans:
            if _cancelled:
                logger.info('cancelling submission, %d plans not scheduled',
                            len(flat_plans))
                break

            done = []
            for plan in flat_plans:
                if plan.is_ready() or plan.is_dead():
                    plan.status.started = True
                    plan.status.future = ex.submit(execute_single_plan, plan)
                    submitted += 1
                    done.append(plan)

            for plan in done:
                flat_plans.remove(plan)

            time.sleep(WORKER_STATUS_POLL_WAIT)

        logger.debug('%d plans submitted', submitted)

    logger.debug('plan submission finished')


# see also: https://github.com/tqdm/tqdm#redirecting-writing
class DummyTqdmFile(object):
    def __init__(self, bar, dest=sys.stdout):
        self.bar = bar
        self.dest = dest

    def write(self, line):
        line = line.rstrip()
        if line:
            self.bar.write(line, file=self.dest)


def execute_plans(plan_dict, workers=1):
    global _cancelled, _cancelled_ack, _killed, _killed_ack
    # collapse tree into a list
    # we'll initially prioritize everything by level, so top-level plans will
    # be run (concurrently) first, followed by their children in the next
    # level, etc
    # in each iteration  we'll scan through the list and ask plans if they are
    # ready to execute. if so, we will pop these from the list and submit them
    # to the executor
    # if not, they'll keep their position in the list and will be tested on
    # the next iteration (in WORKER_STATUS_POLL_WAIT)

    root_plans = []
    for plan_sublist in plan_dict.values():
        root_plans.extend(plan_sublist)

    flat_plans = flatten([], root_plans)
    #for plan in flat_plans:
    #    print plan

    submission_thread = Thread(target=submission_thread_func,
                               args=(flat_plans, workers))
    submission_thread.start()

    bar_format = '{desc}{percentage:3.0f}% |{bar}| {n_fmt}/{total_fmt} {postfix}]'
    bars = {}
    position = 0
    for module, plans in plan_dict.iteritems():
        step_count = sum([plan.total_progress for plan in plans])
        bar = tqdm(desc=module, total=step_count,
                   bar_format=bar_format, position=position,
                   file=sys.stderr, dynamic_ncols=True)

        bars[module] = bar
        position += 1

    stream_handler.stream = DummyTqdmFile(bars.values()[0])

    while submission_thread.isAlive():
        if _cancelled and not _cancelled_ack:
            cancelled_count = 0
            running_count = 0
            for plan in flat_plans:
                if plan.status.finished:
                    continue

                if plan.status.future:
                    if plan.status.future.cancel():
                        cancelled_count += 1
                        plan.status.finished = True
                        plan.status.cancelled = True
                        plan.status.current = plan.status.total
                    else:
                        running_count += 1
                else:
                    plan.status.finished = True
                    plan.status.current = plan.status.total
                    cancelled_count += 1

            logger.info('%d submitted plans cancelled, %d still active',
                        cancelled_count, running_count)
            _cancelled_ack = True

        if _killed and not _killed_ack:
            req_count = 0
            for plan in flat_plans:
                if plan.status.future and plan.status.future.running():
                    plan.status.cancel_requests = True
                    req_count += 1

            logger.info('asked %d ongoing plans to stop', req_count)

            _killed_ack = True

        for module, plans in plan_dict.iteritems():
            bar = bars[module]

            current_sum = 0
            active = []
            for plan in plans:
                current_sum += plan.current_progress
                active.extend(plan.active_in_tree())

            bar.n = current_sum

            if len(active) > 1:
                post = ', '.join(p.verb for p in active)
            elif len(active) == 1:
                post = active[0].status.description or ''
            elif current_sum < bar.total:
                post = ' ... waiting ...'
            else:
                post = 'done!'

            if len(post) > 40:
                post = post[:37] + '...'

            bar.postfix = '%-40s' % post
            bar.refresh()

        time.sleep(WORKER_STATUS_POLL_WAIT)

    for bar in bars.values():
        bar.close()

    stream_handler.stream = sys.stdout

    successes = filter(lambda p: p.status.success, flat_plans)
    failures = filter(lambda p: p.status.failed, flat_plans)
    cancelled = filter(lambda p: p.status.finished and p.status.cancelled,
                       flat_plans)

    logger.info('all tasks completed, %d success, %d fail, %d cancelled',
                len(successes), len(failures), len(cancelled))

    submission_thread.join()

    if len(failures) > 0:
        logger.debug('Failures occurred, exiting unsuccessfully')
        sys.exit(1)
    else:
        sys.exit(0)


def cancel_signal_handler(signal, frame):
    global _cancelled, _killed
    if not _cancelled:
        logger.info('caught signal, cancelling remaining tasks')
        _cancelled = True
        return

    if not _killed:
        logger.info('caught second signal, asking ongoing tasks to stop...')
        logger.info('try Ctrl+\\ to quit forcefully')
        _killed = True
        return

    logger.info('got signal, still waiting on running tasks...')


def main():
    load_verbs()

    verb_strs = map(lambda v: '    {:8}  {}'.format(v.name, v.description),
                    sorted(verbs.values()))

    modules = sorted(list_modules(base_path))
    module_str = textwrap.fill(' '.join(modules),
                               initial_indent='    ',
                               subsequent_indent='    ',
                               break_on_hyphens=False)

    epilog = textwrap.dedent('''\
        build arguments, any of:
          verbs:
        {verbs}
          modules:
        {modules}

        verbs and modules can be given in any order;
        modules and verbs will be processed in the order
        given on the command line.
        ''').format(verbs='\n'.join(verb_strs), modules=module_str)

    parser = ArgumentParser(formatter_class=RawDescriptionHelpFormatter,
                            epilog=epilog)
    parser.add_argument('-d', '--debug', action='store_true',
                        help='enable debug logging')
    parser.add_argument('-w', '--workers', default=1, type=int,
                        help='number of parallel workers')
    parser.add_argument('-s', '--show-plans', action='store_true',
                        help='show plan tree before running')
    parser.add_argument('args', nargs='*', metavar='arg',
                        help='build arguments, see below')

    arguments = parser.parse_args()
    arguments.base_path = base_path
    if arguments.debug:
        logging.root.setLevel(logging.DEBUG)

    arguments.verbs = filter(lambda v: v in verbs.keys(), arguments.args)
    arguments.modules = filter(lambda m: m in modules, arguments.args)
    logger.info('Modules: %r', arguments.modules)

    reserved = arguments.verbs + arguments.modules
    arguments.verb_args = filter(lambda a: a not in reserved, arguments.args)
    logger.debug('verb_args = %r', arguments.verb_args)

    verb_args = verb_arguments(arguments.verb_args, arguments.verbs)
    active_verbs = sorted(map(lambda v: verbs[v], arguments.verbs),
                          key=lambda v: v.priority,
                          reverse=True)

    # re-map to show in order for log message
    logger.info('Applying verbs: %r', map(lambda v: v.name, active_verbs))

    plans = {}
    step_count = 0
    for module in arguments.modules:
        plans[module] = build_plan_tree(arguments, verb_args,
                                        module, active_verbs)
        step_count += sum(map(lambda p: p.steps, plans[module]))

    if arguments.show_plans:
        for module, plan_list in plans.items():
            print 'generated plans:', module
            print_plans(plan_list, offset='  ')
            print ''

    logger.info('%d steps generated from input', step_count)

    signal.signal(signal.SIGINT, cancel_signal_handler)  # signal signal
    execute_plans(plans, arguments.workers)


if __name__ == '__main__':
    main()
