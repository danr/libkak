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
import libkak
import utils
import functools
import re


class Langserver(object):

    def __init__(self, filetype, session, pwd, cmd, mock={}):
        self.cbs = {}
        self.diagnostics = defaultdict(dict)
        self.session = session
        self.client_editing = {}
        self.filetype = filetype

        print(filetype, ' spawns ', cmd)

        if cmd in mock:
            self.proc = mock[cmd]
        else:
            self.proc = Popen(cmd.split(), stdin=PIPE,
                              stdout=PIPE, stderr=sys.stderr)

        t = Thread(target=Langserver.spawn, args=(self, session, pwd))
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

    def spawn(self, session, pwd):

        rootUri = 'file://' + pwd

        @self.call('initialize', {
            'processId': os.getpid(),
            'rootUri': rootUri,
            'rootPath': pwd,
            'capabilities': {}
        })
        def initialized(msg):
            result = msg['result']
            capabilities = result.get('capabilities', {})
            try:
                signatureHelp = capabilities['signatureHelpProvider']
                self.sig_help_chars = signatureHelp['triggerCharacters']
            except KeyError:
                self.sig_help_chars = []

            try:
                completionProvider = capabilities['completionProvider']
                self.complete_chars = completionProvider['triggerCharacters']
            except KeyError:
                self.complete_chars = []

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
                if msg.get('method') == 'textDocument/publishDiagnostics':
                    self.publish_diagnostics(msg.get('params', {}))

    def publish_diagnostics(self, msg):
        buffile = utils.uri_to_file(msg['uri'])
        client = self.client_editing.get(buffile)
        if not client:
            return
        r = libkak.Remote.onclient(self.session, client, sync=False)
        r.arg_config['disabled'] = (
            'kak_opt_lsp_' + self.filetype + '_disabled_diagnostics',
            libkak.Args.string)

        @r
        def _(timestamp, pipe, disabled):
            self.diagnostics[buffile] = defaultdict(list)
            self.diagnostics[buffile]['timestamp'] = timestamp
            flags = [str(timestamp), '1|   ']
            from_severity = [
                '',
                '{red}>> ',
                '{yellow}>> ',
                '{blue}>> ',
                '{green}>> '
            ]
            for diag in msg['diagnostics']:
                if disabled and re.match(disabled, diag['message']):
                    continue
                (line0, col0), end = utils.range(diag['range'])
                flags.append(str(line0) + '|' +
                             from_severity[diag.get('severity', 1)])
                self.diagnostics[buffile][line0].append({
                    'col': col0,
                    'end': end,
                    'message': diag['message']
                })
            # todo: Set for the other buffers too (but they need to be opened)
            res = 'try %{add-highlighter flag_lines default lsp_flags}\n'
            res += 'set buffer=' + buffile + ' lsp_flags '
            res += utils.single_quoted(':'.join(flags))
            pipe(res)
