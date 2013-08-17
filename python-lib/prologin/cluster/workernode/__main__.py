# -*- encoding: utf-8 -*-
# This file is part of Prologin-SADM.
#
# Copyright (c) 2013 Antoine Pietri <antoine.pietri@prologin.org>
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

import logging
import logging.handlers
import os.path
import optparse
import prologin.log
import prologin.config
import prologin.rpc.client
import prologin.rpc.server
import re
import socket
import time
import tornado
import tornado.gen
import tornado.ioloop
import yaml

from . import operations

ioloop = tornado.ioloop.IOLoop.instance()

class WorkerNode:
    def __init__(self, config):
        self.config = config
        self.interval = config['master']['heartbeat_secs']
        self.hostname = socket.gethostname()
        self.port = config['worker']['port']
        self.slots = self.max_slots = config['worker']['available_slots']
        self.min_srv_port = config['worker']['port_range_start']
        self.max_srv_port = config['worker']['port_range_end']
        self.srv_port = self.min_srv_port
        self.matches = {}

        ioloop.add_callback(self.send_heartbeat)

    def get_worker_infos(self):
        return (self.hostname, self.port, self.slots, self.max_slots)

    @property
    def master(self):
        config = self.config
        host, port = (config['master']['host'], config['master']['port'])
        url = "http://{}:{}/".format(host, port)
        return prologin.rpc.client.Client(url,
                secret=config['master']['shared_secret'].encode('utf-8'))

    def available_server_port(self):
        """
        Be optimistic and hope that:
        - nobody will use the ports in the port range but us
        - there will never be more servers than ports in the range
        """
        port = self.srv_port
        self.srv_port += 1
        if self.srv_port > self.max_srv_port:
            self.srv_port = self.min_srv_port
        return port

    @tornado.gen.engine
    def send_heartbeat(self):
        logging.info('sending heartbeat to the server, %d/%d slots' % (
                         self.slots, self.max_slots
        ))
        first_heartbeat = True
        while True:
            try:
                self.master.heartbeat(self.get_worker_infos(), first_heartbeat)
                first_heartbeat = False
            except socket.error:
                msg = 'master down, retrying heartbeat in %ds' % self.interval
                logging.warn(msg)

            yield tornado.gen.Task(ioloop.add_timeout, time.time() +
                    self.interval)

    def start_work(self, work, slots, *args, **kwargs):
        if self.slots < slots:
            logging.warn('not enough slots to start the required job')
            return False, self.get_worker_infos()

        logging.debug('starting a job for %d slots' % slots)
        self.slots -= slots

        def real_work():
            job_done = True
            try:
                job_done, func, args_li = work(*args, **kwargs)
            finally:
                if job_done:
                    self.slots += slots
            func(self.get_worker_infos(), *args_li)

        ioloop.add_callback(real_work)
        return True, self.get_worker_infos()

    def compile_champion(self, contest, user, champ_id):
        ret = operations.compile_champion(self.config, contest, user, champ_id)
        return True, self.master.compilation_result, (champ_id, ret)

    def run_server(self, rep_port, pub_port, contest, match_id, opts=''):
        logging.info('starting server for match %d' % match_id)
        operations.run_server(self.config, worker.server_done, rep_port,
                            pub_port, contest, match_id, opts)
        return False, self.master.match_ready, (match_id, rep_port, pub_port)

    def server_done(self, retcode, stdout, match_id):
        self.slots += 1

        logging.info('match %d done' % match_id)

        lines = stdout.split('\n')
        result = []
        score_re = re.compile(r'^(\d+) (-?\d+) (-?\d+)$')
        for line in lines:
            m = score_re.match(line)
            if m is None:
                continue
            pid, score, stat = m.groups()
            result.append((int(pid), int(score)))

        try:
            self.master.match_done(self.get_worker_infos(), match_id, result)
        except socket.error:
            pass

    def run_client(self, contest, match_id, ip, req_port, sub_port, user, champ_id, pl_id, opts):
        logging.info('running champion %d from %s for match %d' % (
                         champ_id, user, match_id
        ))
        operations.run_client(self.config, ip, req_port, sub_port, contest, match_id, user,
                              champ_id, pl_id, opts, self.client_done)
        return False, self.master.client_ready, (match_id, pl_id)

    def client_done(self, retcode, stdout, match_id, champ_id, pl_id):
        self.slots += 2
        logging.info('champion %d for match %d done' % (champ_id, match_id))
        try:
            self.master.client_done(self.get_worker_infos(),
                                    match_id, pl_id, retcode)
        except socket.error:
            pass

class WorkerNodeProxy(prologin.rpc.server.BaseRPCApp):
    """
    Proxies RPC requests to the WorkerNode.
    """

    def __init__(self, *args, node=None, **kwargs):
        self.node = node
        super().__init__(*args, **kwargs)

    @prologin.rpc.server.remote_method
    def available_server_port(self):
        return self.node.available_server_port()

    @prologin.rpc.server.remote_method
    def compile_champion(self, *args, **kwargs):
        logging.debug('received a compile_champion request')
        return self.node.start_work(
            self.node.compile_champion, 1, *args, **kwargs
        )

    @prologin.rpc.server.remote_method
    def run_server(self, *args, **kwargs):
        logging.debug('received a run_server request')
        return self.node.start_work(
            self.node.run_server, 1, *args, **kwargs
        )

    @prologin.rpc.server.remote_method
    def run_client(self, *args, **kwargs):
        logging.debug('received a run_client request')
        return self.node.start_work(
            self.node.run_client, 2, *args, **kwargs
        )

def read_config(filename):
    return yaml.load(open(filename))

if __name__ == '__main__':
    parser = optparse.OptionParser()
    parser.add_option('-l', '--local-logging', action='store_true',
                      dest='local_logging', default=False,
                      help='Activate logging to stdout.')
    parser.add_option('-v', '--verbose', action='store_true',
                      dest='verbose', default=False,
                      help='Verbose mode.')
    options, args = parser.parse_args()

    prologin.log.setup_logging('worker-node', verbose=options.verbose,
                               local=options.local_logging)

    config = prologin.config.load('worker-node')

    worker = WorkerNode(config)
    s = WorkerNodeProxy(app_name='worker-node', node=worker,
            secret=config['master']['shared_secret'].encode('utf-8'))
    s.listen(config['worker']['port'])

    try:
        ioloop.start()
    except KeyboardInterrupt:
        pass
