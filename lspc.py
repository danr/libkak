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


def esc(cs,s):
    for c in cs:
        s=s.replace(c, "\\"+c)
    return s


def info_somewhere(kak, msg, pos, where):
    """
    @type kak: libkak.Kak
    """
    if msg:
        if where == 'cursor':
            kak.info(msg, libkak.Flag('placement', 'above'),
                          libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
        elif where == 'info':
            kak.info(msg)
        elif where == 'docsclient':
            with tempfile.NamedTemporaryFile() as tmp:
                open(tmp.name, 'wb').write(libkak.encode(msg))
                print(open(tmp.name, 'r').read())
                kak.send("""
                    eval -try-client %opt[docsclient] %[
                        edit! -scratch '*doc*'
                        exec |cat<space> {}<ret>
                        exec \%|fmt<space> - %val[window_width] <space> -s <ret>
                        exec gg
                        set buffer filetype rst
                        try %[rmhl number_lines]
                    ]""".format(tmp.name))
                kak.sync()
    kak.release()


def main_for_filetype(kak, _spawned=set()):
    filetype = kak.opt.filetype()

    if filetype in _spawned:
        print(filetype + ' already spawned')
        kak.release()
        return

    cmd, pwd = kak.ask(kak.opt['lsp_' + filetype + '_cmd'], kak.env.PWD)
    kak.release()
    if not cmd:
        print(filetype + ' has no command')
        return

    print(filetype + ' spawns ' + cmd)

    _spawned.add(filetype)
    lsp=Popen(cmd.split(), stdin=PIPE, stdout=PIPE, stderr=sys.stderr)

    cbs = {}
    diagnostics = defaultdict(list)
    opened = set()

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
        if ft != filetype:
            ctx.release()
            return lambda _: None
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
        if method:
            d = locals()
            q = Queue()
            call(method, params(d), q.put)
            return lambda k: k(q.get(), d)
        else:
            return None


    def initialized(result):
        kak.sync()

        def lsp_sync(ctx):
            """
            Synchronize the current file.

            Hooked automatically to NormalBegin and WinDisplay.
            """
            ctx.echo(libkak.Flag('debug'), 'sync')
            handler(ctx, None, {})
            ctx.release()


        kak.hook('global', 'InsertEnd', group='lsp')(lambda ctx, _: lsp_sync(ctx))
        kak.hook('global', 'WinDisplay', group='lsp')(lambda ctx, _: lsp_sync(ctx))
        kak.cmd(hidden=False)(lsp_sync)

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
                        try:
                            # for pyls
                            sn = result['activeSignature']
                            pn = result['signatures'][sn]['activeParameter']
                            label = result['signatures'][sn]['params'][pn]['label']
                        except:
                            label = str(result)
                    if label:
                        ctx.sync()
                        ctx.info(label, libkak.Flag('placement', 'above'), libkak.Flag('anchor', '{}.{}'.format(pos['line']+1, pos['character']+1)))
                    ctx.release()

        except KeyError:
            pass

        try:
            compl_chars = result['capabilities']['completionProvider']['triggerCharacters']
            filter = u'[' + u''.join(compl_chars) + u']'

            def lsp_complete(ctx):
                """
                Complete at the main cursor.

                Sets the variable lsp_completions.
                """
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
                    buffile=ctx.val.buffile()
                    ctx.opt.assign('lsp_completions', compl, 'set buffer={}'.format(buffile))
                    ctx.release()

            kak.hook('global', 'InsertChar', filter, group='lsp')(lambda ctx, _char: lsp_complete(ctx))
            kak.cmd(hidden=False)(lsp_complete)

        except KeyError:
            pass


        @kak.cmd(hidden=False)
        def lsp_diagnostics(ctx, where):
            """
            Describe diagnostics for a line somewhere ('cursor', 'info'
            or 'docsclient'.)

            Hook this to NormalIdle if you want:

            hook -group lsp global NormalIdle .* %{
                lsp_diagnostics cursor
            }
            """
            ts, line = ctx.ask(ctx.val.timestamp, ctx.val.cursor_line)
            if ts == diagnostics['ts']:
                if diagnostics[line]:
                    min_col = 98765
                    msgs = []
                    for d in diagnostics[line]:
                        if d['col'] < min_col:
                            min_col = d['col']
                        msgs.append(d['message'])
                    info_somewhere(ctx,
                                   '\n'.join(msgs),
                                   {'line': line-1, 'character': min_col-1},
                                   where)
            ctx.release()


        @kak.cmd(hidden=False)
        def lsp_hover(ctx, where):
            """
            Display hover information somewhere ('cursor', 'info' or
            'docsclient'.)

            Hook this to NormalIdle if you want:

            hook -group lsp global NormalIdle .* %{
                lsp_hover cursor
            }
            """
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
                info_somewhere(ctx, label, pos, where)


        @kak.cmd(hidden=False)
        def lsp_references(ctx):
            """
            Find the references to the identifier at the main cursor.
            """
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
            """
            Goto the definition of the identifier at the main cursor.
            """
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
        if line.startswith('Header:  '):
            line = line[len('Header:  '):]
        if line:
            header, value = line.split(":")
            if header == "Content-Length":
                contentLength = int(value)
        else:
            content = lsp.stdout.read(contentLength).decode('utf-8')
            try:
                msg = json.loads(content)
            except Exception:
                msg = "Error deserializing server output: " + content
                print(msg, file=sys.stderr)
                continue
            print(json.dumps(msg, indent=2))
            if 'id' in msg and msg['id'] in cbs:
                cb = cbs[msg['id']]
                del cbs[msg['id']]
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


def main(kak):
    """
    @type kak: libkak.Kak
    """
    kak.remove_hooks('global', 'lsp')
    kak.send('try %{declare-option completions lsp_completions}')
    #kak.send('set-option global completers option=lsp_completions')
    kak.send('try %{declare-option line-flags lsp_flags}')
    kak.send('try %{add-highlighter flag_lines default lsp_flags}')
    kak.sync() # need this because Kakoune quoting is broken
    kak.hook('global', 'WinSetOption', filter='filetype=.*', group='lsp')(lambda ctx, _: main_for_filetype(ctx))
    kak.hook('global', 'WinDisplay', filter='.*', group='lsp')(lambda ctx, _: main_for_filetype(ctx))
    main_for_filetype(kak)


if __name__ == '__main__':
    main(libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0'))
