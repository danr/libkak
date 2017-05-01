from __future__ import print_function
from subprocess import Popen, PIPE
import sys
import json
import uuid
from functools import wraps
from pprint import pprint
import libkak
import os
import tempfile
from multiprocessing import Queue


# mighty globals!!
cbs = {}


def craft(method, params, cb=None, _private={'n':0}):
    n = _private['n']
    obj = {
        'jsonrpc': '2.0',
        'id': n,
        'method': method,
        'params': params
        }
    if cb:
        cbs[n] = cb
    _private['n'] += 1
    payload=json.dumps(obj)
    return u"Content-Length: {0}\r\n\r\n{1}".format(len(payload), payload).encode('utf-8')


def position(kak):
    """
    @type kak: libkak.Kak
    """
    line, column = kak.ask(kak.val.cursor_line, kak.val.cursor_column)
    return {
        'line': line-1,
        'character': column-1
        }


def main(kak, cmd=['node', '/home/dan/build/javascript-typescript-langserver/lib/language-server-stdio.js']):
    """
    @type kak: libkak.Kak
    """
    kak.remove_hooks('global', 'lsp')
    lsp=Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=sys.stderr)
    def call(method, params, cb=None):
        msg = craft(method, params, cb)
        lsp.stdin.write(msg)
        lsp.stdin.flush()
        print(msg)

    def initialized(result):
        try:
            sig_help_chars = result['capabilities']['signatureHelpProvider']['triggerCharacters']
            kak.sync()
            @kak.hook('global', 'InsertChar', u'[' + u''.join(sig_help_chars) + u']', group='lsp')
            def _(ctx, _char):
                pos = position(ctx)
                buffile = ctx.val.buffile()
                ts = ctx.val.timestamp()
                with tempfile.NamedTemporaryFile() as tmp:
                    ctx.evaluate('write ' + tmp.name)
                    ctx.sync()
                    contents = libkak.decode(open(tmp.name, 'r').read())
                    ctx.release()
                call('textDocument/didChange', {
                    'textDocument': {
                        'uri': 'file://' + buffile,
                        'version': ts
                        },
                    'contentChanges': [{'text': contents}]
                    })
                q = Queue()
                call('textDocument/signatureHelp', {
                    'textDocument': {'uri': 'file://' + buffile},
                    'position': pos
                    }, q.put)
                result = q.get()
                try:
                    label = result['signatures'][result['activeSignature']]['parameters'][result['activeParameter']]['label']
                except:
                    label = str(result)
                ctx.sync()
                ctx.info(label, libkak.Flag('placement', 'above'), libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
                ctx.release()

        except KeyError:
            pass

        @kak.hook('global', 'NormalIdle', group='lsp')
        def _(ctx, _):
            pos = position(ctx)
            buffile = ctx.val.buffile()
            ts = ctx.val.timestamp()
            with tempfile.NamedTemporaryFile() as tmp:
                ctx.evaluate('write ' + tmp.name)
                ctx.sync()
                contents = libkak.decode(open(tmp.name, 'r').read())
                ctx.release()
            call('textDocument/didChange', {
                'textDocument': {
                    'uri': 'file://' + buffile,
                    'version': ts
                    },
                'contentChanges': [{'text': contents}]
                })
            q = Queue()
            call('textDocument/hover', {
                'textDocument': {'uri': 'file://' + buffile},
                'position': pos
                }, q.put)
            result = q.get()
            try:
                label = result['contents'][0]['value']
            except:
                return
            print(str(result))
            print(ctx.val.client())
            ctx.info(label, libkak.Flag('placement', 'above'), libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
            ctx.release()

        kak.release()


    pwd = kak.env.PWD()
    kak.release()
    rootUri = 'file://' + pwd
    call('initialize', {
        'processId': os.getpid(),
        'rootUri': rootUri,
        'capabilities': {}
        }, initialized)

    contentLength = 0
    while not lsp.stdout.closed:
        line = lsp.stdout.readline().decode('utf-8').strip()
        print('line:', line)
        if line.startswith('Header:  '):
            line = line[len('Header:  '):]
            print('line:', line)
        if line:
            header, value = line.split(":")
            if header == "Content-Length":
                contentLength = int(value)
        else:
            content = lsp.stdout.read(contentLength).decode('utf-8')
            print('content:', content)
            try:
                msg = json.loads(content)
            except Exception:
                msg = "Error deserializing server output: " + content
                print(msg, file=sys.stderr)
                continue
            #try:
            if True:
                print(json.dumps(msg, indent=2))
                if 'id' in msg and msg['id'] in cbs:
                    cb = cbs[msg['id']]
                    del cbs[msg['id']]
                    print(cb.__name__)
                    cb(msg['result'])
            #except Exception as e:
                #msg = "Error handling message: " + content
                #msg += '\n' + str(e)
                #msg += str(dir(e))
                #print(msg, file=sys.stderr)






if __name__ == '__main__':
    main(libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0'))
