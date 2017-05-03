from __future__ import print_function
from subprocess import Popen, PIPE
import sys
import json
import libkak
import os
import tempfile
from multiprocessing import Queue
from collections import defaultdict
import six


def jsonrpc(obj):
    obj['jsonrpc'] = '2.0'
    msg = json.dumps(obj)
    msg = u"Content-Length: {0}\r\n\r\n{1}".format(len(msg), msg)
    return msg.encode('utf-8')


def esc(cs, s):
    for c in cs:
        s = s.replace(c, "\\" + c)
    return s


def format_pos(pos):
    """
    >>> format_pos({'line': 5, 'character': 0})
    6.1
    """
    return '{}.{}'.format(pos['line'] + 1, pos['character'] + 1)


def info_somewhere(kak, msg, pos, where):
    """
    @type kak: libkak.Kak
    """
    if msg:
        if where == 'cursor':
            kak.info(msg,
                     libkak.Flag('placement', 'above'),
                     libkak.Flag('anchor', format_pos(pos)))
        elif where == 'info':
            kak.info(msg)
        elif where == 'docsclient':
            with tempfile.NamedTemporaryFile() as tmp:
                open(tmp.name, 'wb').write(libkak.encode(msg))
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


def complete_item(item):
    return (item['label'],
            '{}\n\n{}'.format(item['detail'], item['documentation']),
            '{} [{}]'.format(item['label'], item.get('kind', '?')))


def complete_items(items):
    return ('|'.join(esc('|:', x) for x in complete_item(item))
            for item in items)


def pyls_signatureHelp(result, pos):
    sn = result['activeSignature']
    pn = result['signatures'][sn].get('activeParameter', -1)
    func_label = result['signatures'][sn]['label']
    params = result['signatures'][sn]['params']
    return nice_sig(func_label, params, pn, pos)


def nice_sig(func_label, params, pn, pos):
    func_name, _ = func_label.split('(', 1)
    try:
        _, func_type = func_label.rsplit(')', 1)
    except ValueError:
        func_type = ''
    param_labels = [
        ('*' if i == pn else '') + param['label'] +
        ('*' if i == pn else '')
        for i, param in enumerate(params)
    ]
    label = func_name + '(' + ', '.join(param_labels) + ')' + func_type
    pos['character'] -= len(func_name) + 1
    pos['character'] -= len(', '.join(param_labels[:pn]))
    if pn > 0:
        pos['character'] -= 1
    if pos['character'] < 0:
        pos['character'] = 0
    return label


class MockStdio(object):
    def __init__(self, q):
        self.q = q
        self.closed = False

    def write(self, msg):
        for c in msg:
            self.q.put(chr(c) if isinstance(c,int) else c)

    def flush(self):
        pass

    def readline(self):
        cs = []
        while True:
            c = self.q.get()
            cs.append(c)
            if c == '\n':
                break
        return libkak.encode(''.join(cs))

    def read(self, n):
        cs = []
        for _ in range(n):
            c = self.q.get()
            cs.append(c)
        return libkak.encode(''.join(cs))


class MockPopen(object):
    def __init__(self, q_in, q_out):
        self.stdin = MockStdio(q_in)
        self.stdout = MockStdio(q_out)


def main_for_filetype(kak, mock=None, _spawned=set()):
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
    if mock:
        lsp = mock
    else:
        lsp = Popen(cmd.split(), stdin=PIPE, stdout=PIPE, stderr=sys.stderr)

    cbs = {}
    diagnostics = defaultdict(list)
    opened = set()

    def craft(method, params, cb=None, _private={'n': 0}):
        n = '{}-{}'.format(method, _private['n'])
        obj = {
            'id': n,
            'method': method,
            'params': params
        }
        if cb:
            cbs[n] = cb
        _private['n'] += 1
        return jsonrpc(obj)

    def call(method, params, cb=None):
        msg = craft(method, params, cb)
        lsp.stdin.write(msg)
        lsp.stdin.flush()
        print('sent:', method)

    def handler(ctx, method, params, extra_cmd=''):
        """
        # this function should use the filetype's call method,
        # which communicates with the correct service,
        # and put the callback on that service's callback queue
        # then everything pretty much works, because
        # after this function everything is the same for all filetypes

        # todo another day

        @type ctx: libkak.Kak
        """
        ctx.send("eval -draft '")
        ctx.send(extra_cmd)
        line, column, buffile, ts, ft = ctx._ask([
            ctx.val.cursor_line,
            ctx.val.cursor_column,
            ctx.val.buffile,
            ctx.val.timestamp,
            ctx.opt.filetype
        ], extra_manager=ctx.end_quote)
        if ft != filetype:
            ctx.release()
            return lambda _: None
        with tempfile.NamedTemporaryFile() as tmp:
            ctx.evaluate('write ' + tmp.name, libkak.Flag('no-hooks'))
            ctx.sync()
            contents = libkak.decode(open(tmp.name, 'r').read())
            ctx.release()
        q = Queue()
        pos = {'line': line - 1, 'character': column - 1}
        uri = 'file://' + buffile
        if uri in opened:
            call('textDocument/didChange', {
                'textDocument': {'uri': uri, 'version': ts},
                'contentChanges': [{'text': contents}]
            }, q.put)
        else:
            call('textDocument/didOpen', {
                 'textDocument': {
                     'uri': uri,
                     'version': ts,
                     'languageId': ft,
                     'text': contents
                 },
                 }, q.put)
            opened.add(uri)
        q.get()
        if method:
            d = locals()
            call(method, params(d), q.put)
            def _cont(k):
                r = q.get()
                print('got didX for', method)
                return k(r, d)
            return _cont
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

        kak.hook('global', 'InsertEnd', group='lsp')(lambda k, _: lsp_sync(k))
        kak.hook('global', 'WinDisplay', group='lsp')(lambda k, _: lsp_sync(k))
        kak.cmd()(lsp_sync)

        try:
            signatureHelp = result['capabilities']['signatureHelpProvider']
            sig_help_chars = signatureHelp['triggerCharacters'] + [',']
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
                        active = result['signatures'][result['activeSignature']]
                        pn = result['activeParameter']
                        func_label = active.get('label', '')
                        params = active['parameters']
                        label = nice_sig(func_label, params, pn, pos)
                    except KeyError:
                        try:
                            label = pyls_signatureHelp(result, pos)
                        except KeyError:
                            if not result.get('signatures'):
                                label = ''
                            else:
                                label = str(result)
                    if label:
                        ctx.sync()
                        info_somewhere(ctx, label, pos, 'cursor')
                    ctx.release()

        except KeyError:
            pass

        try:
            completionProvider = result['capabilities']['completionProvider']
            compl_chars = completionProvider['triggerCharacters']
            filter = u'[' + u''.join(compl_chars) + u']'

            def lsp_complete(ctx, extra_cmd):
                """
                Complete at the main cursor, after performing an optional
                extra_cmd in a -draft evalutaion context. The extra cmd can be
                used to set the cursor at the right place, like so:

                map global insert <a-c> '<a-;>:lsp_complete %(exec b)<ret>'

                If you don't want to use it set it to the empty string.

                Sets the variable lsp_completions.
                """
                @handler(ctx, 'textDocument/completion', lambda d: {
                    'textDocument': {'uri': d['uri']},
                    'position': d['pos']
                }, extra_cmd=extra_cmd)
                def _(result, d):
                    pos = d['pos']
                    cs = ':'.join(complete_items(result['items']))
                    compl = '{}@{}:{}'.format(format_pos(pos), d['ts'], cs)
                    buffile = ctx.val.buffile()
                    ctx.opt.assign('lsp_completions', compl,
                                   'set buffer=' + buffile)
                    ctx.release()

            kak.hook('global', 'InsertChar', filter, group='lsp')(
                lambda ctx, _char: lsp_complete(ctx, ''))
            kak.cmd()(lsp_complete)
            kak.hook

        except KeyError:
            pass

        @kak.cmd()
        def lsp_diagnostics(ctx, where,
                            ts=kak.val.timestamp,
                            line=kak.val.cursor_line):
            """
            Describe diagnostics for the cursor line somewhere
            ('cursor', 'info' or 'docsclient'.)

            Hook this to NormalIdle if you want:

            hook -group lsp global NormalIdle .* %{
                lsp_diagnostics cursor
            }
            """
            if ts == diagnostics['ts']:
                if diagnostics[line]:
                    min_col = 98765
                    msgs = []
                    for d in diagnostics[line]:
                        if d['col'] < min_col:
                            min_col = d['col']
                        msgs.append(d['message'])
                    pos = {'line': line - 1, 'character': min_col - 1}
                    info_somewhere(ctx, '\n'.join(msgs), pos, where)
            ctx.release()

        @kak.cmd()
        def lsp_diagnostics_jump(ctx, direction,
                                 ts=kak.val.timestamp,
                                 line=kak.val.cursor_line):
            """
            Jump to next or prev diagnostic (relative to the main cursor line)

            Example configuration:

            map global user n ':lsp_diagonstics_jump next<ret>:lsp_diagnostics cursor<ret>'
            map global user p ':lsp_diagonstics_jump prev<ret>:lsp_diagnostics cursor<ret>'
            """
            if ts == diagnostics['ts']:
                next_line = None
                first_line = None
                last_line = None
                for other_line in six.iterkeys(diagnostics):
                    if other_line == 'ts':
                        continue
                    if not first_line or other_line < first_line:
                        first_line = other_line
                    if not last_line or other_line > last_line:
                        last_line = other_line
                    if next_line:
                        if direction == 'prev':
                            cmp = next_line < other_line < line
                        else:
                            cmp = next_line > other_line > line
                    else:
                        if direction == 'prev':
                            cmp = other_line < line
                        else:
                            cmp = other_line > line
                    if cmp:
                        next_line = other_line
                if not next_line and direction == 'prev':
                    next_line = last_line
                if not next_line and direction == 'next':
                    next_line = first_line
                if next_line:
                    y = next_line
                    x = diagnostics[y][0]['col']
                    ctx.select([((y, x), (y, x))])
            else:
                lsp_sync(ctx)

        @kak.cmd()
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

        @kak.cmd()
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
                        col0 = int(loc['range']['start']['character']) + 1
                        line1 = int(loc['range']['end']['line']) + 1
                        col1 = int(loc['range']['end']['character'])
                        c.append(((line0, col0), (line1, col1)))
                    else:
                        other += 1
                if c:
                    ctx.select(c)
                    if other:
                        msg = 'Also at {} positions in other files'.format(other)
                        ctx.echo(msg)
                    ctx.release()
                else:
                    print('got no results', result)
                    ctx.release()

        @kak.cmd()
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

                if not result:
                    ctx.sync()
                    ctx.echo(libkak.Flag('color', 'red'), "No results!")
                    ctx.release()
                    return

                c = []
                for loc in result:
                    line0 = int(loc['range']['start']['line']) + 1
                    col0 = int(loc['range']['start']['character']) + 1
                    line1 = int(loc['range']['end']['line']) + 1
                    col1 = int(loc['range']['end']['character'])
                    c.append((loc['uri'], (line0, col0), (line1, col1)))

                ctx.sync()
                sel = ctx.menu([u'{}:{}'.format(uri, line0)
                                for (uri, (line0, _), _) in c])
                (uri, p0, p1) = c[sel]
                if uri.startswith('file://'):
                    uri = uri[len('file://'):]

                    ctx.send('edit', uri)
                    ctx.select([(p0, p1)])
                else:
                    ctx.echo(libkak.Flag('color', 'red'),
                             "Cannot open {}".format(uri))
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
            # typescript-langserver has this extra Header:
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
            print('\n'.join(json.dumps(msg, indent=2).split('\n')[:40]))
            if msg.get('id') in cbs:
                cb = cbs[msg['id']]
                del cbs[msg['id']]
                if 'error' in msg:
                    print('error', file=sys.stderr)
                else:
                    cb(msg.get('result'))
            if msg.get('method') == 'textDocument/publishDiagnostics':
                kak.sync()
                msg = msg['params']
                buffile, ts = kak.ask(kak.val.buffile, kak.val.timestamp)
                if msg['uri'] == 'file://' + buffile:
                    diagnostics.clear()
                    diagnostics['ts'] = ts
                    flags = [str(ts), '1|   ']
                    from_severity = [
                        '',
                        '{red+b}>> ',
                        '{yellow+b}>> ',
                        '{blue}>> ',
                        '{green}>> '
                    ]
                    for diag in msg['diagnostics']:
                        line0 = int(diag['range']['start']['line']) + 1
                        col0 = int(diag['range']['start']['character']) + 1
                        flags.append(str(line0) + '|' +
                                     from_severity[diag.get('severity', 1)])
                        diagnostics[line0].append({
                            'col': col0,
                            'message': diag['message']
                        })
                    # Can set for other buffers, but they need to be opened
                    kak.opt.assign('lsp_flags', ':'.join(flags),
                                   'set buffer=' + buffile)
                kak.release()


def main(kak, mock=None):
    """
    @type kak: libkak.Kak
    """
    kak.remove_hooks('global', 'lsp')
    kak.send('try %{declare-option completions lsp_completions}')
    # kak.send('set-option global completers option=lsp_completions')
    kak.send('try %{declare-option line-flags lsp_flags}')
    kak.send('try %{add-highlighter flag_lines default lsp_flags}')
    kak.sync()  # need this because Kakoune quoting is broken
    kak.hook('global', 'WinSetOption', filter='filetype=.*', group='lsp')(
        lambda ctx, _: main_for_filetype(ctx, mock))
    kak.hook('global', 'WinDisplay', filter='.*', group='lsp')(
        lambda ctx, _: main_for_filetype(ctx, mock))
    main_for_filetype(kak, mock)


def test(debug=False):
    import time
    from threading import Thread

    p, q = Queue(), Queue()
    kak_mock = MockPopen(p, q)
    kak = libkak.headless(debug=debug, ui='json' if debug else 'dummy')
    kak.send('declare-option str filetype test')
    kak.send('declare-option str lsp_test_cmd mock')
    kak.send('set global completers option=lsp_completions')
    kak.sync()
    kak2 = libkak.Kak('pipe', kak._pid, 'unnamed0', debug=debug)
    t = Thread(target=main, args=(kak2, kak_mock))
    t.daemon = True
    t.start()

    kak.release()

    lsp_mock = MockPopen(p, q)

    def getobj():
        line = lsp_mock.stdin.readline()
        header, value = line.split(b":")
        assert(header == b"Content-Length")
        cl = int(value)
        lsp_mock.stdin.readline()
        import json
        obj = json.loads(lsp_mock.stdin.read(cl).decode('utf-8'))
        print(json.dumps(obj, indent=2))
        return obj
    obj = getobj()
    assert(obj['method'] == 'initialize')
    lsp_mock.stdout.write(jsonrpc({
        'id': obj['id'],
        'result': {
            'capabilities': {
                'signatureHelpProvider': {
                    'triggerCharacters': ['(', ',']
                },
                'completionProvider': {
                    'triggerCharacters': ['.']
                }
            }
        }
    }))
    time.sleep(1)  # wait for triggers and definition to be set up
    kak.execute('itest.')
    kak.release()
    obj = getobj()
    assert(obj['method'] == 'textDocument/didOpen')
    assert(obj['params']['textDocument']['text'] == 'test.\n')
    lsp_mock.stdout.write(jsonrpc({
        'id': obj['id'],
        'result': None
    }))
    obj = getobj()
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})
    lsp_mock.stdout.write(jsonrpc({
        'id': obj['id'],
        'result': {
            'items': [
                {
                    'label': 'apa',
                    'kind': 3,
                    'documentation': 'monkey function',
                    'detail': 'call the monkey',
                },
                {
                    'label': 'bepa',
                    'kind': 4,
                    'documentation': 'monkey constructor',
                    'detail': 'construct a monkey',
                }
            ]
        }
    }))

    time.sleep(1)
    kak.execute('<c-n><c-n><esc>%')
    kak.sync()
    s = kak.val.selection()
    kak.quit()
    print(s)
    assert(s == 'test.bepa\n')


if __name__ == '__main__':
    if '--test' in sys.argv:
        test()
    else:
        kak = libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0',
                         debug='-v' in sys.argv)
        main(kak)

