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


# mighty global of callbacks
cbs = {}


def esc(cs,s):
    for c in cs:
        s=s.replace(c, "\\"+c)
    return s


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


    def handler(ctx, method, params):
        """
        @type ctx: libkak.Kak
        """
        line, column, buffile, ts = ctx.ask(ctx.val.cursor_line, ctx.val.cursor_column, ctx.val.buffile, ctx.val.timestamp)
        with tempfile.NamedTemporaryFile() as tmp:
            ctx.evaluate('write ' + tmp.name)
            ctx.sync()
            contents = libkak.decode(open(tmp.name, 'r').read())
            ctx.release()
        pos = {'line': line-1, 'character': column-1}
        uri = 'file://' + buffile
        call('textDocument/didChange', {
            'textDocument': {'uri': uri, 'version': ts},
            'contentChanges': [{'text': contents}]
            })
        d = locals()
        q = Queue()
        print('locals:', d)
        call(method, params(d), q.put)
        return lambda k: k(q.get(), d)


    def initialized(result):
        kak.sync()
        try:
            sig_help_chars = result['capabilities']['signatureHelpProvider']['triggerCharacters']
            filter = u'[' + u''.join(sig_help_chars) + u']'
            @kak.hook('global', 'InsertChar', filter, group='lsp')
            def _(ctx, _char):
                @handler(ctx, 'textDocument/signatureHelp', lambda d: {
                    'textDocument': {'uri': d['uri']},
                    'position': d['pos']
                    })
                def _(result, d):
                    pos = d['pos']
                    try:
                        label = result['signatures'][result['activeSignature']]['parameters'][result['activeParameter']]['label']
                    except:
                        label = str(result)
                    ctx.sync()
                    ctx.info(label, libkak.Flag('placement', 'above'), libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
                    ctx.release()

        except KeyError:
            pass

        try:
            compl_chars = result['capabilities']['completionProvider']['triggerCharacters']
            filter = u'[' + u''.join(compl_chars) + u']'
            @kak.hook('global', 'InsertChar', filter, group='lsp')
            def _(ctx, _char):
                @handler(ctx, 'textDocument/completion', lambda d: {
                    'textDocument': {'uri': d['uri']},
                    'position': d['pos']
                    })
                def _(result, d):
                    pos = d['pos']
                    cs = (
                        '|'.join(esc('|:', x) for x in
                            (item['label'],
                             '{}\n\n{}'.format(item['detail'], item['documentation']),
                             '{} [{}]'.format(item['label'], item['kind'])))
                        for item in result['items']
                    )
                    compl = '{}.{}@{}:{}'.format(
                        pos['line']+1, pos['character']+1, d['ts'],
                        ':'.join(cs))
                    print(compl)
                    print(ctx.val.timestamp())
                    ctx.sync()
                    ctx.opt.lsc_completions = compl
                    ctx.release()

        except KeyError:
            pass


        @kak.hook('global', 'NormalIdle', group='lsp')
        def _(ctx, _):
            @handler(ctx, 'textDocument/hover', lambda d: {
                'textDocument': {'uri': d['uri']},
                'position': d['pos']
                })
            def _result(result, d):
                pos = d['pos']
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
    kak.send('declare-option -hidden completions lsc_completions')
    kak.send('set-option -add completers option=lsc_completions')
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
