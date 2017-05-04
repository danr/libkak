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


class Langserver(object):
    @staticmethod
    def for_filetype(kak, filetype, mock, _spawned=dict()):
        if filetype in _spawned:
            print(filetype + ' already spawned')
            kak.release()
            return _spawned[filetype]

        else:
            cmd, pwd = kak.ask(kak.opt['lsp_' + filetype + '_cmd'], kak.env.PWD)
            kak.release()
            if not cmd:
                print(filetype + ' has no command')
                return None

            _spawned[filetype] = Langserver(filetype, mock, cmd, pwd)
            return _spawned[filetype]


    def __init__(self, filetype, mock, cmd, pwd):
        self.cbs = {}

        print(filetype + ' spawns ' + cmd)

        if cmd == 'mock':
            self.proc = mock
        else:
            self.proc = Popen(cmd.split(), stdin=PIPE, stdout=PIPE, stderr=sys.stderr)

        from threading import Thread
        t = Thread(target=Langserver.spawn, args=(self, pwd))
        t.start()
        print('thread', t, 'started for' , self.proc)


    def craft(self, method, params, cb=None, _private={'n': 0}):
        """
        Assigns to cbs
        """
        n = '{}-{}'.format(method, _private['n'])
        obj = {
            'id': n,
            'method': method,
            'params': params
        }
        if cb:
            self.cbs[n] = cb
        _private['n'] += 1
        return jsonrpc(obj)


    def call(self, method, params):
        """
        Assigns to cbs
        """
        def k(cb=None):
            msg = self.craft(method, params, cb)
            self.proc.stdin.write(msg)
            self.proc.stdin.flush()
            print('sent:', method)
        return k


    def spawn(self, pwd):

        rootUri = 'file://' + pwd
        @self.call('initialize', {
            'processId': os.getpid(),
            'rootUri': rootUri,
            'rootPath': pwd,
            'capabilities': {}
        })
        def initialized(result):
            """
            Make a register new complete char function that sets up a hook
            that calls lsp_signature_help/lsp_complete if that char
            is not hooked up yet (for efficiency)

            Or when filetype is set, (re-)make the hooks

            Can have a buffer-specific option with sig-help and complete-help
            specific setup hooks
            """

            try:
                signatureHelp = result['capabilities']['signatureHelpProvider']
                self.sig_help_chars = signatureHelp['triggerCharacters']
            except KeyError:
                self.sig_help_chars = []

            try:
                completionProvider = result['capabilities']['completionProvider']
                self.complete_chars = completionProvider['triggerCharacters']
            except KeyError:
                self.complete_chars = []


        contentLength = 0
        while not self.proc.stdout.closed:
            line = self.proc.stdout.readline().decode('utf-8').strip()
            if line.startswith('Header:  '):
                # typescript-langserver has this extra Header:
                line = line[len('Header:  '):]
            if line:
                header, value = line.split(":")
                if header == "Content-Length":
                    contentLength = int(value)
            else:
                content = self.proc.stdout.read(contentLength).decode('utf-8')
                try:
                    msg = json.loads(content)
                except Exception:
                    msg = "Error deserializing server output: " + content
                    print(msg, file=sys.stderr)
                    print('closed:', self.proc.stdout.closed)
                    continue
                print('Response from langserver:', '\n'.join(json.dumps(msg, indent=2).split('\n')[:40]))
                if msg.get('id') in self.cbs:
                    cb = self.cbs[msg['id']]
                    del self.cbs[msg['id']]
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

    def lsp_sync_hook(ctx, _):
        ctx.send('lsp_sync')
        ctx.release()

    kak.hook('global', 'InsertEnd', group='lsp')(lsp_sync_hook)
    kak.hook('global', 'WinSetOption', filter='filetype=.*', group='lsp')(lsp_sync_hook)
    kak.hook('global', 'WinDisplay', group='lsp')(lsp_sync_hook)

    diagnostics = defaultdict(list)
    opened = set()

    def handler(ctx, method, params, extra_cmd='', _timestamps={}):
        """
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
        langserver = Langserver.for_filetype(ctx, ft, mock)
        if not langserver:
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
        if _timestamps.get(buffile) == ts:
            print('no need to send update')
        else:
            _timestamps[buffile] = ts
            if uri in opened:
                langserver.call('textDocument/didChange', {
                    'textDocument': {'uri': uri, 'version': ts},
                    'contentChanges': [{'text': contents}]
                })(q.put)
            else:
                langserver.call('textDocument/didOpen', {
                     'textDocument': {
                         'uri': uri,
                         'version': ts,
                         'languageId': ft,
                         'text': contents
                     }
                 })(q.put)
                opened.add(uri)
            q.get()
        if method:
            d = locals()
            langserver.call(method, params(d))(q.put)
            def _cont(k):
                r = q.get()
                print('got didX for', method)
                return k(r, d)
            return _cont
        else:
            return None


    @kak.cmd()
    def lsp_sync(ctx):
        """
        Synchronize the current file.

        Hooked automatically to NormalBegin and WinDisplay.
        """
        ctx.echo(libkak.Flag('debug'), 'sync')
        handler(ctx, None, {})
        ctx.release()


    @kak.cmd()
    def lsp_signature_help(ctx, _char):
        """
        Write signature help by the cursor.
        """
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

    @kak.hook('global', 'InsertChar', group='lsp')
    def _(ctx, char):
        ft = ctx.opt.filetype()
        ls = Langserver.for_filetype(ctx, ft, mock)
        if ls and char in ls.sig_help_chars:
            ctx.send('lsp_signature_help')
        ctx.release()

    @kak.hook('global', 'InsertChar', group='lsp')
    def _(ctx, char):
        ft = ctx.opt.filetype()
        ls = Langserver.for_filetype(ctx, ft, mock)
        print('InsertChar', char, ft, ls, ls and ls.complete_chars)
        if ls and char in ls.complete_chars:
            ctx.send('lsp_complete ""')
        ctx.release()

    @kak.cmd()
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

    kak.send('lsp_sync')
    kak.release()


if __name__ == '__main__':
    kak = libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0',
                     debug='-v' in sys.argv)
    main(kak)

