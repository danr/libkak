# -*- coding: utf-8 -*-

from __future__ import print_function
from collections import defaultdict, OrderedDict
from six.moves.queue import Queue
from subprocess import Popen, PIPE
from threading import Thread
import pprint
import itertools as it
import json
import os
import six
import sys
import tempfile
import utils
import functools
import re


class Langserver(object):

    def __init__(self, pwd, cmd, push=None, mock={}):
        self.cbs = {}
        self.diagnostics = defaultdict(dict)
        self.push = push or utils.noop
        self.pwd = pwd

        if cmd in mock:
            self.proc = mock[cmd]
        else:
            self.proc = Popen(cmd.split(), stdin=PIPE,
                              stdout=PIPE, stderr=sys.stderr)

        t = Thread(target=Langserver.spawn, args=(self,))
        t.start()
        print('thread', t, 'started for', self.proc)

    def craft(self, method, params, cb=None, _private={'n': 0}):
        """
        Assigns to cbs
        """
        obj = {
            'method': method,
            'params': params
        }
        if cb:
            n = '{}-{}'.format(method, _private['n'])
            obj['id'] = n
            self.cbs[n] = cb
            _private['n'] += 1
        return utils.jsonrpc(obj)

    def call(self, method, params):
        """
        craft assigns to cbs
        """

        def k(cb=None):
            msg = self.craft(method, params, cb)
            self.proc.stdin.write(msg)
            self.proc.stdin.flush()
            print('sent:', method)
        return k

    def spawn(self):

        rootUri = 'file://' + self.pwd

        self.call('initialize', {
            'processId': os.getpid(),
            'rootUri': rootUri,
            'rootPath': self.pwd,
            'capabilities': {}
        })(lambda msg: self.push('initialize', msg.get('result', {})))

        contentLength = 0
        while not self.proc.stdout.closed:
            line = self.proc.stdout.readline().decode('utf-8').strip()
            # typescript-langserver has this extra Header:
            line = utils.drop_prefix(line, 'Header:  ')
            if line:
                header, value = line.split(":")
                if header == "Content-Length":
                    contentLength = int(value)
            else:
                content = self.proc.stdout.read(contentLength).decode('utf-8')
                try:
                    msg = json.loads(content)
                except Exception:
                    if not self.proc.stdout.closed:
                        msg = "Error deserializing server output: " + content
                        print(msg, file=sys.stderr)
                    continue
                print('Response from langserver:', '\n'.join(
                    pprint.pformat(msg).split('\n')[:40]))
                if msg.get('id') in self.cbs:
                    cb = self.cbs[msg['id']]
                    del self.cbs[msg['id']]
                    if 'error' in msg:
                        print('error', pprint.pformat(msg), file=sys.stderr)
                    cb(msg)
                if 'id' not in msg and 'method' in msg:
                    self.push(msg['method'], msg.get('params'))

