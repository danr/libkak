from __future__ import print_function
from subprocess import Popen, PIPE
import sys
import json
import libkak
import os
import tempfile
from multiprocessing import Queue
from collections import defaultdict, OrderedDict
import six
import itertools as it


def jsonrpc(obj):
    obj['jsonrpc'] = '2.0'
    msg = json.dumps(obj)
    msg = u"Content-Length: {0}\r\n\r\n{1}".format(len(msg), msg)
    return msg.encode('utf-8')


def select(cursors):
    return ':'.join('%d.%d,%d.%d' % tuple(it.chain(*pos)) for pos in cursors)


def menu(options):
    return 'menu -auto-single ' + ' '.join(it.chain(*x) for x in options)


def esc(cs, s):
    for c in cs:
        s = s.replace(c, "\\" + c)
    return s


def single_quote_escape(string):
    """
    Backslash-escape ' and \.
    """
    return string.replace(u"\\'", u"\\\\'").replace(u"'", u"\\'")


def single_quoted(string):
    u"""
    The string wrapped in single quotes and escaped.

    >>> print(single_quoted(u"i'ié"))
    'i\\'ié'
    """
    return u"'" + single_quote_escape(string) + u"'"




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
    def for_filetype(kak, filetype, handler, mock={}, _spawned=dict()):
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

            _spawned[filetype] = Langserver(filetype, handler, pwd, cmd, mock=mock)
            return _spawned[filetype]


    def __init__(self, filetype, handler, pwd, cmd, mock={}):
        self.cbs = {}
        self.handler = handler

        print(filetype + ' spawns ' + cmd)

        if cmd in mock:
            self.proc = mock[cmd]
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
        craft assigns to cbs
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
                    @self.handler(call_immediately=True)
                    def _(buffile, timestamp):
                        msg = msg['params']
                        if msg['uri'] == 'file://' + buffile:
                            diagnostics.clear()
                            diagnostics['ts'] = timestamp
                            flags = [str(timestamp), '1|   ']
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
                            # todo: Set for the other buffers too (but they need to be opened)
                            return 'set buffer=' + buffile + ' lsp_flags ' + ':'.join(flags)


def main(session, mock={}):
    """
    @type kak: libkak.Kak
    """

    def pipe_to_kak(msg):
        p=Popen(['kak', '-p', session.rstrip()], stdin=PIPE)
        print(msg)
        p.communicate(msg.encode('utf-8'))

    diagnostics = defaultdict(list)
    hooks_setup = set()


    # todo: split this into two (one jsonrpc-lsp related and one kak related)
    def handler(method=None, make_params=None, params='0', before_sending='', call_immediately=False, _timestamps={}):
        def inner(f):
            args = [
                ('line',      'kak_cursor_line',   int),
                ('column',    'kak_cursor_column', int),
                ('buffile',   'kak_buffile',       noop),
                ('timestamp', 'kak_timestamp',     int),
                ('filetype',  'kak_opt_filetype',  noop),
                ('client',    'kak_client',        noop),
                ('temp',      '1',                 noop),
            ]

            fifo = os.path.join(dir, f.__name__)

            msg = "def -allow-override -params {params} -docstring {docstring} {name}".format(
                name = f.__name__,
                params = params,
                docstring = single_quoted(f.__docstring__))

            if call_immediately:
                msg = "eval"

            msg += r""" %(
                    eval -draft %(
                        {before_sending}
                        %sh(
                           temp=$(mktemp)
                           echo eval -no-hooks write $temp
                           params=""
                           for param; do params="${{params}}_s${{param//_/_u}}" done
                           params="${{params//\n/_n}}"
                           echo {name}_continue $temp "${{params}}"
                        )
                    )
                )

                def -allow-override -hidden -params 2 {name}_continue %(
                    %sh(
                        echo "{argsplice}$2" > {fifo}
                        # if we want a synchronous reply read from some
                        # fifo here that python will write to
                        # benefit: editor locks while waiting for the reply
                        #          we know when editor is ready
                        # disadvantage: locking could go wrong, or for too long,
                        #               complicates implementation
                        # LSP seems to only need async, however
                    )
                )
                """.format(
                    name = f.__name__,
                    before_sending = before_sending,
                    argsplice = '_s'.join('${' + splice + '//_/_u}'
                                          for _, splice, _ in args),
                    fifo = fifo)

            pipe_to_kak(msg)

            @fork
            def listen():
                # todo: while true if not call_immediately
                with open(fifo, 'r') as fp:
                    params = [v.replace('_n', '\n').replace('_u', '_')
                              for v in fp.readline().split('_s')]
                r = {}
                actual_params = []
                for arg, value in it.izip_longest(args, params)
                    try:
                        name, _, parse = arg
                        r[name] = parse(value)
                    except:
                        actual_params.append(value)

                r['pos'] = {'line': r['line'] - 1, 'character': r['column'] - 1}
                r['uri'] = 'file://' + r['buffile']

                langserver = Langserver.for_filetype(r['filetype'], mock)
                if not langserver:
                    return
                r['langserver'] = langserver

                q = Queue()

                def sync_contents(filetype, buffile, timestamp, uri, temp):
                    old_timestamp = _timestamps.get((filetype, buffile))
                    if old_timestamp == timestamp:
                        print('no need to send update')
                    else:
                        _timestamps[(filetype, buffile)] = timestamp
                        with open(temp, 'r') as fp:
                            contents = fp.read()
                        if old_timestamp is None:
                            langserver.call('textDocument/didOpen', {
                                 'textDocument': {
                                     'uri': uri,
                                     'version': timestamp,
                                     'languageId': filetype,
                                     'text': contents
                                 }
                             })(q.put)
                        else:
                            langserver.call('textDocument/didChange', {
                                'textDocument': {
                                    'uri': uri,
                                    'version': timestamp
                                },
                                'contentChanges': [{'text': contents}]
                            })(q.put)
                        q.get()

                sync_contents(**r)
                if method:
                    langserver.call(method, make_params(**r))(q.put)
                    r['result'] = q.get()
                x = f(*actual_params, **r)
                if x:
                    pipe_to_kak(x)

            import functools
            @functools.wraps(f)
            def call_from_python(*args):
                escaped = [single_quoted(arg) for arg in args]
                pipe_to_kak(' '.join([f.__name__] + escaped))
            return call_from_python

        return inner


    @handler()
    def lsp_sync(buffile, langserver):
        """
        Synchronize the current file.

        Makes sure that:
            * the language server is registered at the language server,
            * the language server has an up-to-date view on the buffer
              (even if it is not saved).
            * the window has hooks set up for complete & sig help

        Hooked automatically to NormalBegin and WinDisplay.
        """
        msg = 'echo -debug sync'
        if buffile not in hooks_setup:
            hooks_setup.add(buffile)
            sig = langserver.sig_help_chars
            if sig:
                msg += '\nhook -group lsp buffer={} InsertChar [{}] lsp_signature_help'.format(buffile, ''.join(sig))
            compl = d['langserver'].complete_chars
            if compl:
                msg += '\nhook -group lsp buffer={} InsertChar [{}] %[lsp_complete ""]'.format(buffile, ''.join(compl))
        return msg


    @handler('textDocument/signatureHelp',
             lambda pos, uri:
                {'textDocument': {'uri': uri},
                 'position': pos})
    def lsp_signature_help(pos, uri, result):
        """
        Write signature help by the cursor.
        """
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
        return info_somewhere(label, pos, 'cursor')


    @handler('textDocument/completion',
             lambda pos, uri:
                {'textDocument': {'uri': uri},
                 'position': pos},
             before_sending='%arg{1}',
             params='1')
    def lsp_complete(_, pos, timestamp, buffile, result):
        """
        Complete at the main cursor, after performing an optional
        extra_cmd in a -draft evalutaion context. The extra cmd can be
        used to set the cursor at the right place, like so:

        map global insert <a-c> '<a-;>:lsp_complete %(exec b)<ret>'

        If you don't want to use it set it to the empty string.

        Sets the variable lsp_completions.
        """
        def _(result, d):
            pos = d['pos']
            cs = ':'.join(complete_items(result['items']))
            compl = '{}@{}:{}'.format(format_pos(pos), timestamp, cs)
            return 'set buffer=' + buffile + ' lsp_completions ' + single_quoted(compl)

    @handler(params='1')
    def lsp_diagnostics(where, timestamp, line):
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
                return info_somewhere('\n'.join(msgs), pos, where)

    @handler(params='1')
    def lsp_diagnostics_jump(direction, timestamp, line):
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
                return select([((y, x), (y, x))])
        else:
            return 'lsp_sync'


    @handler('textDocument/hover',
             lambda pos, uri:
                {'textDocument': {'uri': uri},
                 'position': pos},
             params='1')
    def lsp_hover(where, pos, uri, result):
        """
        Display hover information somewhere ('cursor', 'info' or
        'docsclient'.)

        Hook this to NormalIdle if you want:

        hook -group lsp global NormalIdle .* %{
            lsp_hover cursor
        }
        """
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
                label.append(content)
        label = '\n\n'.join(label)
        return info_somewhere(ctx, label, pos, where)

    @handler('textDocument/references',
             lambda pos, uri:
                {'textDocument': {'uri': uri},
                 'position': pos,
                 'includeDeclaration': True})
    def lsp_references(uri, result):
        """
        Find the references to the identifier at the main cursor.
        """
        c = []
        other = 0
        for loc in result:
            if loc['uri'] == uri:
                line0 = int(loc['range']['start']['line']) + 1
                col0 = int(loc['range']['start']['character']) + 1
                line1 = int(loc['range']['end']['line']) + 1
                col1 = int(loc['range']['end']['character'])
                c.append(((line0, col0), (line1, col1)))
            else:
                other += 1
        if c:
            msg = select(c)
            if other:
                msg += '\necho Also at {} positions in other files'.format(other)
            return msg
        else:
            print('got no results', result)

    @handler('textDocument/definition',
             lambda pos, uri:
                {'textDocument': {'uri': uri},
                 'position': pos})
    def lsp_goto_definition(ctx):
        """
        Goto the definition of the identifier at the main cursor.
        """
        if 'uri' in result:
            result = [result]

        if not result:
            return 'echo -color -red No results!'

        c = []
        for loc in result:
            line0 = int(loc['range']['start']['line']) + 1
            col0 = int(loc['range']['start']['character']) + 1
            line1 = int(loc['range']['end']['line']) + 1
            col1 = int(loc['range']['end']['character'])
            c.append((loc['uri'], (line0, col0), (line1, col1)))

        options = []
        for uri, p0, p1 in c:
            if uri.startswith('file://'):
                uri = uri[len('file://'):]
                action = 'edit {}; {}'.format(uri, select([(p0, p1)]))
            else:
                action = 'echo -color red Cannot open {}'.format(uri)
            line0, _ = p0
            options.append((u'{}:{}'.format(uri, line0), action))
        return menu(options)


    pipe_to_kak("""#kak
    remove-hooks global lsp
    try %{declare-option completions lsp_completions}
    # set-option global completers option=lsp_completions'
    try %{declare-option line-flags lsp_flags}
    try %{add-highlighter flag_lines default lsp_flags}

    hook -group lsp global InsertEnd .* lsp_sync
    hook -group lsp global WinSetOption filetype=.* lsp_sync
    hook -group lsp global WinDisplay .* lsp_sync

    # sync with all open buffers (yes!)
    %sh{
        echo eval -buffer %{kak_buflist//:/,} lsp_sync
    }
    """)



if __name__ == '__main__':
    kak = libkak.Kak('pipe', int(sys.argv[1]), 'unnamed0',
                     debug='-v' in sys.argv)
    main(kak)

