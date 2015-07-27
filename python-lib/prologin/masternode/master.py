# -*- encoding: utf-8 -*-
# This file is part of Prologin-SADM.
#
# Copyright (c) 2013-2015 Antoine Pietri <antoine.pietri@prologin.org>
# Copyright (c) 2011 Pierre Bourdon <pierre.bourdon@prologin.org>
# Copyright (c) 2011 Association Prologin <info@prologin.org>
#
# Prologin-SADM is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Prologin-SADM is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Prologin-SADM.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import copy
import json
import logging
import os.path
import prologin.rpc.server
import random
import time

from base64 import b64decode, b64encode
from pathlib import Path

from .concoursquery import ConcoursQuery
from .monitoring import (
    masternode_bad_result,
    masternode_client_done_file,
    masternode_match_done_db,
    masternode_match_done_file,
    masternode_request_compilation_task,
    masternode_task_redispatch,
    masternode_worker_timeout,
)
from .task import MatchTask, CompilationTask
from .task import champion_compiled_path, match_path, clog_path
from .worker import Worker

class MasterNode(prologin.rpc.server.BaseRPCApp):
    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config
        self.workers = {}
        self.worker_tasks = []
        self.db = ConcoursQuery(config)
        self.spawn_tasks()

    def spawn_tasks(self):
        self.to_dispatch = asyncio.Event()
        self.janitor = asyncio.Task(self.janitor_task())
        self.dbwatcher = asyncio.Task(self.dbwatcher_task())
        self.dispatcher = asyncio.Task(self.dispatcher_task())

    @prologin.rpc.remote_method
    def status(self):
        d = []
        for (host, port), w in self.workers.items():
            d.append((host, port, w.slots, w.max_slots))
        return d

    @asyncio.coroutine
    def register_worker(self, key, w):
        if (yield from w.reachable()):
            logging.warn("registered new worker: {}:{}".format(w.hostname,
                w.port))
            self.workers[key] = w
        else:
            logging.warn("drop unreachable worker: {}:{}".format(w.hostname,
                w.port))

    @prologin.rpc.remote_method
    def update_worker(self, worker):
        hostname, port, slots, max_slots = worker
        key = hostname, port
        if key not in self.workers:
            w = Worker(hostname, port, slots, max_slots, self.config)
            asyncio.Task(self.register_worker(key, w))
        else:
            logging.debug("updating worker: {}:{} {}/{}".format(
                              hostname, port, slots, max_slots))
            self.workers[key].update(slots, max_slots)

    @prologin.rpc.remote_method
    def heartbeat(self, worker, first):
        hostname, port, slots, max_slots = worker
        usage = (1.0 - slots / max_slots)
        logging.debug('received heartbeat from {}:{}, usage is {:.2%}'.format(
                         hostname, port, usage))
        if first and (hostname, port) in self.workers:
            self.redispatch_worker(self.workers[(hostname, port)])
        self.update_worker(worker)

    @prologin.rpc.remote_method
    def compilation_result(self, worker, cid, user, ret, b64compiled, log):
        def complete_compilation(cid, user, ret):
            status = 'ready' if ret else 'error'
            if ret:
                with open(champion_compiled_path(self.config, user, cid), 'wb') as f:
                    f.write(b64decode(b64compiled))
            with open(clog_path(self.config, user, cid), 'w') as f:
                f.write(log)
            logging.info('compilation of champion {}: {}'.format(cid, status))
            yield from self.db.execute('set_champion_status',
                    {'champion_id': cid, 'champion_status': status})

        hostname, port, slots, max_slots = worker
        w = self.workers[(hostname, port)]
        w.remove_compilation_task(cid)
        asyncio.Task(complete_compilation(cid, user, ret))

    @prologin.rpc.remote_method
    def match_done(self, worker, mid, result, dumper_stdout, server_stdout,
                   players_stdout):
        logging.info('match {} ended'.format(mid))

        # Write player logs
        for pl_id, (champ_id, log) in players_stdout.items():
            logname = 'log-champ-{}-{}.log'.format(pl_id, champ_id)
            logpath = os.path.join(match_path(self.config, mid), logname)
            with masternode_client_done_file.time(), \
                 open(logpath, 'wb') as fplayer:
                fplayer.write(b64decode(log))

        # Write server logs and dumper log
        serverpath = os.path.join(match_path(self.config, mid), 'server.log')
        dumppath = os.path.join(match_path(self.config, mid), 'dump.json.gz')
        with masternode_match_done_file.time(), \
             open(serverpath, 'w') as fserver, \
             open(dumppath, 'wb') as fdump:
                fserver.write(server_stdout)
                fdump.write(b64decode(dumper_stdout))

        # Update the database with the results
        @asyncio.coroutine
        def match_done_db(mid, mstatus, pscores, tscores):
            start = time.monotonic()
            yield from self.db.execute('set_match_status', mstatus)
            yield from self.db.executemany('set_player_score', pscores)
            yield from self.db.executemany('update_tournament_score', tscores)
            masternode_match_done_db.observe(time.monotonic() - start)

        try:
            match_status = { 'match_id': mid, 'match_status': 'done' }
            player_scores = [{ 'player_id': r['player'],
                   'player_score': r['score'] }
                    for r in result]
            tournament_scores = [{ 'match_id': mid,
                       'champion_score': r['score'],
                       'player_id': r['player'] }
                    for r in result]
            asyncio.Task(match_done_db(mid, match_status, player_scores,
                                       tournament_scores))
        except KeyError:
            masternode_bad_result.inc()
            return

        # Remove task from worker
        self.workers[(worker[0], worker[1])].remove_match_task(mid)

    def redispatch_worker(self, worker):
        masternode_task_redispatch.inc(len(worker.tasks))
        if worker.tasks:
            logging.info("redispatching tasks for {}: {}".format(
                             worker, worker.tasks))
            self.worker_tasks = worker.tasks + self.worker_tasks
            self.to_dispatch.set()
        del self.workers[(worker.hostname, worker.port)]

    @asyncio.coroutine
    def janitor_task(self):
        while True:
            for worker in list(self.workers.values()):
                if not worker.is_alive(self.config['worker']['timeout_secs']):
                    masternode_worker_timeout.inc()
                    logging.warn("timeout detected for worker {}".format(
                                worker))
                    self.redispatch_worker(worker)
            yield from asyncio.sleep(1)

    @asyncio.coroutine
    def check_requested_compilations(self, status='new'):
        to_set_pending = []
        cur = yield from self.db.execute('get_champions',
            { 'champion_status': status })

        for r in (yield from cur.fetchall()):
            logging.info('requested compilation for {} / {}'.format(r[1], r[0]))
            masternode_request_compilation_task.inc()
            to_set_pending.append({
                'champion_id': r[0],
                'champion_status': 'pending'
            })
            t = CompilationTask(self.config, r[1], r[0])
            self.worker_tasks.append(t)

        if to_set_pending:
            self.to_dispatch.set()
        yield from self.db.executemany('set_champion_status', to_set_pending)

    @asyncio.coroutine
    def check_requested_matches(self, status='new'):
        to_set_pending = []
        c = yield from self.db.execute('get_matches', {'match_status': status})

        for r in (yield from c.fetchall()):
            logging.info('request match id {} launch'.format(r[0]))
            mid = r[0]
            opts_json = r[1]
            file_opts_json = r[2]
            players = list(zip(r[3], r[4], r[5]))

            opts = {}
            if opts_json:
                try:
                    opts = json.loads(opts_json)
                except (TypeError, ValueError) as e:
                    logging.warning('cannot decode the custom options json,'
                                    'assuming it is empty', exc_info=1)

            file_opts = {}
            if file_opts_json:
                try:
                    file_opts_paths = json.loads(file_opts_json)
                except (TypeError, ValueError) as e:
                    logging.warning('cannot decode the custom options json,'
                                    'assuming it is empty', exc_info=1)
            for k, path in file_opts_paths.items():
                try:
                    file_opts[k] = b64encode(open(path, 'rb').read()).decode()
                except FileNotFoundError:
                    logging.warning(
                            'file for option {} not found: {}'.format(k, path))

            to_set_pending.append({
                'match_id': mid,
                'match_status': 'pending',
            })
            t = MatchTask(self.config, mid, players, opts, file_opts)
            self.worker_tasks.append(t)

        if to_set_pending:
            self.to_dispatch.set()
        yield from self.db.executemany('set_match_status', to_set_pending)

    @asyncio.coroutine
    def dbwatcher_task(self):
        yield from self.check_requested_compilations('pending')
        yield from self.check_requested_matches('pending')
        while True:
            yield from self.check_requested_compilations()
            yield from self.check_requested_matches()
            yield from asyncio.sleep(1)

    def find_worker_for(self, task):
        available = list(self.workers.values())
        available = [w for w in available if w.can_add_task(task)]
        random.shuffle(available)
        available.sort(key=lambda w: w.usage)
        if not available:
            return None
        else:
            return available[0]

    @asyncio.coroutine
    def dispatcher_task(self):
        while True:
            yield from self.to_dispatch.wait()
            if self.worker_tasks:
                task = self.worker_tasks[0]
                w = self.find_worker_for(task)
                if w is None:
                    logging.info("no worker available for task {}".format(task))
                else:
                    w.add_task(self, task)
                    logging.debug("task {} got to {}".format(task, w))
                    self.worker_tasks = self.worker_tasks[1:]
            if not self.worker_tasks:
                self.to_dispatch.clear()
            else:
                yield from asyncio.sleep(1) # No worker available, wait 1s

            # Give the hand back to the event loop to avoid being blocking,
            # but be called as soon as all the functions at the top of the heap
            # have been executed
            yield from asyncio.sleep(0)