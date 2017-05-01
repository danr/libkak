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
from collections import defaultdict


# mighty global of callbacks
cbs = {}
diagnostics = defaultdict(list)
opened = set()


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


def main(kak, cmd):
    """
    @type kak: libkak.Kak
    """
    kak.remove_hooks('global', 'lsp')
    kak.send('try %{declare-option completions lsp_completions}')
    kak.send('set-option global completers option=lsp_completions')
    kak.send('try %{declare-option -hidden line-flags lsp_flags}')
    kak.send('try %{add-highlighter flag_lines default lsp_flags}')
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
        line, column, buffile, ts, ft = ctx.ask(ctx.val.cursor_line, ctx.val.cursor_column, ctx.val.buffile, ctx.val.timestamp, ctx.opt.filetype)
        with tempfile.NamedTemporaryFile() as tmp:
            ctx.evaluate('write ' + tmp.name, libkak.Flag('no-hooks'))
            ctx.sync()
            contents = libkak.decode(open(tmp.name, 'r').read())
            ctx.release()
        pos = {'line': line-1, 'character': column-1}
        uri = 'file://' + buffile
        if uri in opened:
            call('textDocument/didChange', {
                'textDocument': {'uri': uri, 'version': ts},
                'contentChanges': [{'text': contents}]
                })
        else:
            call('textDocument/didOpen', {
                'textDocument': {
                    'uri': uri,
                    'version': ts,
                    'languageId': ft,
                    'text': contents
                    },
                })
            opened.add(uri)
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

            def lsp_complete(ctx):
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
                             '{} [{}]'.format(item['label'], item.get('kind', '?'))))
                        for item in result['items']
                    )
                    compl = '{}.{}@{}:{}'.format(
                        pos['line']+1, pos['character']+1, d['ts'],
                        ':'.join(cs))
                    ctx.sync()
                    ctx.opt.assign('lsp_completions', compl, 'set buffer')
                    ctx.release()

            kak.hook('global', 'InsertChar', filter, group='lsp')(lambda ctx, _char: lsp_complete(ctx))
            kak.cmd(hidden=False)(lsp_complete)

        except KeyError:
            pass


        @kak.hook('global', 'NormalIdle', group='lsp')
        def _(ctx, _):
            # diagnostics
            if ctx.val.timestamp() == diagnostics['ts']:
                line = ctx.val.cursor_line()
                if diagnostics[line]:
                    for d in diagnostics[line]:
                        ctx.info(d['message'],
                                 libkak.Flag('placement', 'above'),
                                 libkak.Flag('anchor', '{}.{}'.format(line, d['col'])))

            @handler(ctx, 'textDocument/hover', lambda d: {
                'textDocument': {'uri': d['uri']},
                'position': d['pos']
                })
            def _result(result, d):
                pos = d['pos']
                label = []
                if not result:
                    return
                contents = result['contents']
                if not isinstance(contents, list):
                    contents = [contents]
                for content in contents:
                    if isinstance(content, dict) and 'value' in content:
                        label.append(content['value'])
                    else:
                        # a string
                        label.append(content)
                label = '\n\n'.join(label)
                print(str(result))
                print(ctx.val.client())
                ctx.info(label, libkak.Flag('placement', 'above'), libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
                ctx.release()

        @kak.cmd(hidden=False)
        def lsp_references(ctx):
            @handler(ctx, 'textDocument/references', lambda d: {
                'textDocument': {'uri': d['uri']},
                'position': d['pos'],
                'includeDeclaration': True
                })
            def _result(result, d):
                c = []
                other = 0
                for loc in result:
                    if loc['uri'] == d['uri']:
                        line0 = int(loc['range']['start']['line']) + 1
                        col0  = int(loc['range']['start']['character']) + 1
                        line1 = int(loc['range']['end']['line']) + 1
                        col1  = int(loc['range']['end']['character'])
                        c.append(((line0, col0), (line1, col1)))
                    else:
                        other += 1
                ctx.select(c)
                if other:
                    ctx.echo('Also at {} positions in other files'.format(other))
                ctx.release()


        @kak.cmd(hidden=False)
        def lsp_goto_definition(ctx):
            @handler(ctx, 'textDocument/definition', lambda d: {
                'textDocument': {'uri': d['uri']},
                'position': d['pos'],
                })
            def _result(result, d):

                if 'uri' in result:
                    result = [result]

                c = []
                for loc in result:
                    line0 = int(loc['range']['start']['line']) + 1
                    col0  = int(loc['range']['start']['character']) + 1
                    line1 = int(loc['range']['end']['line']) + 1
                    col1  = int(loc['range']['end']['character'])
                    c.append((loc['uri'], (line0, col0), (line1, col1)))

                sel = ctx.menu(u'{}:{}'.format(uri, line0) for (uri, (line0, _), _) in c)
                (uri, p0, p1) = c[sel]
                if uri.startswith('file://'):
                    uri = uri[len('file://'):]
                    ctx.send('edit', uri)
                    ctx.select([(p0, p1)])
                else:
                    ctx.echo(libkak.Flag('color', 'red'), "Cannot open {}".format(uri))
                ctx.release()


        kak.release()


    pwd = kak.env.PWD()
    kak.release()
    rootUri = 'file://' + pwd
    call('initialize', {
        'processId': os.getpid(),
        'rootUri': rootUri,
        'rootPath': pwd,
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
            if 'method' in msg and msg['method'] == 'textDocument/publishDiagnostics':
                msg = msg['params']
                if msg['uri'] == 'file://' + kak.val.buffile():
                    ts = kak.val.timestamp()
                    diagnostics.clear()
                    diagnostics['ts'] = ts
                    flags = [str(ts), '1|   ']
                    from_severity = ['',
                        '{red+b}>> ',
                        '{yellow+b}>> ',
                        '{blue}>> ',
                        '{green}>> '
                        ]
                    for diag in msg['diagnostics']:
                        line0 = int(diag['range']['start']['line']) + 1
                        col0  = int(diag['range']['start']['character']) + 1
                        flags.append(str(line0) + '|' + from_severity[diag.get('severity',1)])
                        diagnostics[line0].append({
                            'col': col0,
                            'message': diag['message']
                            })
                    kak.opt.lsp_flags = ':'.join(flags)
                    kak.release()

            #except Exception as e:
                #msg = "Error handling message: " + content
                #msg += '\n' + str(e)
                #msg += str(dir(e))
                #print(msg, file=sys.stderr)






if __name__ == '__main__':
    main(libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0'),
        #cmd=['pyls']
        #cmd=['/home/dan/go/bin/go-langserver']
        cmd=['node', '/home/dan/build/javascript-typescript-langserver/lib/language-server-stdio.js'
    )
