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
from langserver import Langserver


def edit_uri_select(uri, positions):
    filename = utils.uri_to_file(uri)
    if filename:
        return 'edit {}; {}'.format(filename, libkak.select(positions))
    else:
        return 'echo -markup {red}Cannot open {}'.format(uri)

def apply_textedit(textedit):
    return libkak.change(utils.range(textedit['range']), textedit['newText'])

def apply_textdocumentedit(edit):
    filename = utils.uri_to_file(edit['textDocument']['uri'])
    if filename:
        return 'edit {}; {}; write'.format(filename, '; '.join(apply_textedit(textedit)
            for textedit in edit['edits']))
    else:
        return 'echo -markup {red}Cannot open {}'.format(uri)

def apply_workspaceedit(wsedit):
    if wsedit.get('documentChanges', None):
        return '; '.join(apply_textdocumentedit(edit) for edit in wsedit['documentChanges'])
    elif wsedit.get('changes', None):
        return 'echo -markup {red}Language server does not support documentChanges'
    else:
        return 'echo -markup {red}Invalid workspaceedit; echo -debug {}'.format(wsedit)

def format_pos(pos):
    """
    >>> print(format_pos({'line': 5, 'character': 0}))
    6.1
    """
    return '{}.{}'.format(pos['line'] + 1, pos['character'] + 1)


somewhere = 'cursor info docsclient'.split()


def info_somewhere(msg, pos, where):
    """
    where = cursor | info | docsclient
    """
    if not msg:
        return
    msg = msg.rstrip()
    if where == 'cursor':
        return 'info -placement above -anchor {} {}'.format(
            format_pos(pos), utils.single_quoted(utils.join(msg.split('\n')[0:10], '\n')))
    elif where == 'info':
        return 'info ' + utils.single_quoted(utils.join(msg.split('\n')[0:20], '\n'))
    elif where == 'docsclient':
        tmp = tempfile.mktemp()
        open(tmp, 'wb').write(utils.encode(msg))
        return """
            eval -no-hooks -try-client %opt[docsclient] %[
              edit! -scratch '*doc*'
              exec \%d|cat<space> {tmp}<ret>
              exec \%|fmt<space> - %val[window_width] <space> -s <ret>
              exec gg
              set buffer filetype rst
              try %[rmhl number_lines]
              %sh[rm {tmp}]
            ]""".format(tmp=tmp)


completionItemsKind = [
    '',
    'Text',
    'Method',
    'Function',
    'Constructor',
    'Field',
    'Variable',
    'Class',
    'Interface',
    'Module',
    'Property',
    'Unit',
    'Value',
    'Enum',
    'Keyword',
    'Snippet',
    'Color',
    'File',
    'Reference',
    'Folder',
    'EnumMember',
    'Constant',
    'Struct',
    'Event',
    'Operator',
    'TypeParameter',
]


def complete_items(items):
    try:
        maxlen = max(len(item['label']) for item in items)
    except ValueError:
        maxlen = 0
    return (complete_item(item, maxlen) for item in items)


def complete_item(item, maxlen):
    spaces = ' ' * (maxlen - len(item['label']))
    kind_description = completionItemsKind[item.get('kind', 0)]
    if not kind_description:
        # match '(JSX Element)' and 'type' from typescript details
        derived = re.match('(\w+|\(.+?\))', item.get('detail', ''))
        if derived:
            kind_description = derived.group(1)
    menu_entry = item['label'] + spaces + ' {MenuInfo}' + kind_description
    return (
        item['label'],
        '{}\n\n{}'.format(item.get('detail', ''),
                          item.get('documentation', '')[:500]),
        menu_entry
    )


def pyls_signatureHelp(result, pos):
    sn = result['activeSignature']
    pn = result['signatures'][sn].get('activeParameter', -1)
    func_label = result['signatures'][sn]['label']
    params = result['signatures'][sn]['params']
    return nice_sig(func_label, params, pn, pos)


def nice_sig(func_label, params, pn, pos):
    try:
        func_name, _ = func_label.split('(', 1)
    except ValueError:
        func_name = func_label
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

class Client:

    def __init__(self):
        self.langser = None

        self.langservers = {}
        self.timestamps = {}
        self.message_handlers = {}

        self.sig_help_chars = {}
        self.complete_chars = {}
        self.diagnostics = {}
        self.client_editing = {}
        self.original = {}
        self.chars_setup = set()
        self.session = None
        self.mock = None
        self.builders = {}

    def push_message(self, filetype):
        def k(method, params):
            self.message_handlers.get(method, utils.noop)(filetype, params)
        return k

    def make_sync(self, method, make_params):

        def sync(d, line, column, buffile, filetype, timestamp, pwd, cmd, client, reply):

            d['pos'] = {'line': line - 1, 'character': column - 1}
            d['uri'] = uri = 'file://' + six.moves.urllib.parse.quote(buffile)

            if cmd in self.langservers:
                print(filetype + ' already spawned')
            else:
                push = self.push_message(filetype)
                self.langservers[cmd] = Langserver(pwd, cmd, push, self.mock)

            if not client:
                print("Client was empty when syncing")

            d['langserver'] = langserver = self.langservers[cmd]

            old_timestamp = self.timestamps.get((filetype, buffile))
            if old_timestamp == timestamp and not d['force']:
                print('no need to send update')
                reply('')
            else:
                self.timestamps[(filetype, buffile)] = timestamp
                with tempfile.NamedTemporaryFile() as tmp:
                    write = "eval -no-hooks 'write {}'".format(tmp.name)
                    libkak.pipe(reply, write, client=client, sync=True)
                    print('finished writing to tempfile')
                    contents = open(tmp.name, 'r').read()
                self.client_editing[filetype, buffile] = client
                if old_timestamp is None:
                    langserver.call('textDocument/didOpen', {
                        'textDocument': {
                            'uri': uri,
                            'version': timestamp,
                            'languageId': filetype,
                            'text': contents
                        }
                    })()
                else:
                    langserver.call('textDocument/didChange', {
                        'textDocument': {
                            'uri': uri,
                            'version': timestamp
                        },
                        'contentChanges': [{'text': contents}]
                    })()
                print('sync: waiting for didChange reply...')
                print('sync: got didChange reply...')

            if method:
                print(method, 'calling langserver')
                q = Queue()
                langserver.call(method, utils.safe_kwcall(
                    make_params, d))(q.put)
                return q.get()
            else:
                return {'result': None}

        return sync

    def message_handler(self, f):
        self.message_handlers[f.__name__.replace('_', '/')] = f
        return f

    def message_handler_named(self, name):
        def decorator(f):
            self.message_handlers[name] = f
            return f
        return decorator

    def handler(self, method=None, make_params=None, params='0', enum=None, force=False, hidden=False):
        def decorate(f):
            def builder():
                self.original[f.__name__] = f

                r = libkak.Remote(self.session)
                r.command(r, params=params, enum=enum, sync_setup=True, hidden=hidden)
                r_pre = r.pre
                r.pre = lambda f: r_pre(f) + '''
                        [[ -z $kak_opt_filetype ]] && exit
                        while read lsp_cmd; do
                            IFS=':' read -ra x <<< "$lsp_cmd"
                            if [[ $kak_opt_filetype == ${x[0]} ]]; then
                                unset x[0]
                                cmd="${x[@]}"
                                '''
                r.post = '''
                                break
                            fi
                        done <<< "$kak_opt_lsp_servers"''' + r.post
                r.setup_reply_channel(r)
                r.arg_config['cmd'] = ('cmd', libkak.Args.string)
                sync = self.make_sync(method, make_params)
                r.puns = False
                r.argnames = utils.argnames(sync) + utils.argnames(f)

                @functools.wraps(f)
                def k(d):
                    try:
                        d['d'] = d
                        d['force'] = force
                        # print('handler calls sync', pprint.pformat(d))
                        msg = utils.safe_kwcall(sync, d)
                        # print('sync called', status, result, pprint.pformat(d))
                        if 'result' in msg:
                            d['result'] = msg['result']
                            print('Calling', f.__name__, pprint.pformat(d)[:500])
                            msg = utils.safe_kwcall(f, d)
                            if msg:
                                print('Answer from', f.__name__, ':', msg)
                                d['pipe'](msg)
                        else:
                            print('Error: ', msg)
                            d['pipe']('''
                            echo -debug When handling {}:
                            echo -debug {}
                            echo -markup "{{red}}Error from language server (see *debug* buffer)"
                            '''.format(utils.single_quoted(f.__name__),
                                       utils.single_quoted(pprint.pformat(msg))))
                    except:
                        import traceback
                        msg = f.__name__ + ' ' + traceback.format_exc()
                        print(msg)
                        d['pipe']('''
                        echo -debug When handling {}:
                        echo -debug {}
                        echo -markup "{{red}}Error from language client (see *debug* buffer)"
                        '''.format(utils.single_quoted(f.__name__), utils.single_quoted(msg)))

                return r(k)
            self.builders[f.__name__] = builder
            return builder
        return decorate

    def pipe(self, msg, client=None, sync=False):
        libkak.pipe(self.session, msg, client, sync)

    def main(self, session, mock={}, messages=""):
        self.session = session
        self.mock = mock

        for k, builder in self.builders.items():
            builder()

        libkak.pipe(session, """#kak
        remove-hooks global lsp
        try %{declare-option str lsp_servers}
        try %{declare-option str lsp_complete_chars}
        try %{declare-option str lsp_signature_help_chars}
        try %{declare-option completions lsp_completions}
        try %{declare-option line-specs lsp_flags}

        hook -group lsp global InsertChar .* %{
            try %{
                exec -no-hooks -draft <esc><space>h<a-k>[ %opt{lsp_complete_chars} ]<ret>
                lsp-complete
            }
            try %{
                exec -no-hooks -draft <esc><space>h<a-k>[ %opt{lsp_signature_help_chars} ]<ret>
                lsp-signature-help
            }
        }

        # hook -group lsp global WinDisplay .* lsp-sync
        hook -group lsp global BufWritePost .* lsp-send-did-save
        hook -group lsp global BufClose .* lsp-buffer-deleted
        """ + messages)

def makeClient():
    client = Client()

    # Handlers

    @client.message_handler
    def initialize(filetype, result):
        capabilities = result.get('capabilities', {})
        try:
            signatureHelp = capabilities['signatureHelpProvider']
            client.sig_help_chars[filetype] = signatureHelp['triggerCharacters']
        except KeyError:
            client.sig_help_chars[filetype] = []
        except TypeError:
            sig_help_chars[filetype] = []

        try:
            completionProvider = capabilities['completionProvider']
            client.complete_chars[filetype] = completionProvider['triggerCharacters']
        except KeyError:
            client.complete_chars[filetype] = []
        except TypeError:
            sig_help_chars[filetype] = []

    @client.message_handler
    def window_logMessage(filetype, params):

        libkak._debug('Log message', params)

        if 'message' in params:
            libkak._debug('Adding debug print', params)
            libkak.pipe(client.session, 'echo -debug ' + utils.single_quote_escape(params['message']))

        if 'uri' not in params:
            return
        buffile = utils.uri_to_file(params['uri'])
        clientp = client.client_editing.get((filetype, buffile))
        if not clientp:
            return

        libkak.pipe(client.session, 'echo ' + utils.single_quote_escape(params['message']), client=clientp)

    @client.message_handler
    def textDocument_publishDiagnostics(filetype, params):
        buffile = utils.uri_to_file(params['uri'])
        clientp = client.client_editing.get((filetype, buffile))
        if not clientp:
            return
        r = libkak.Remote.onclient(client.session, clientp, sync=False)
        r.arg_config['disabled'] = (
            'kak_opt_lsp_' + filetype + '_disabled_diagnostics',
            libkak.Args.string)

        @r
        def _(timestamp, pipe, disabled):
            client.diagnostics[filetype, buffile] = defaultdict(list)
            client.diagnostics[filetype, buffile]['timestamp'] = timestamp
            flags = [str(timestamp), '1|  ']
            from_severity = [
                u'',
                u'{red}\u2022 ',
                u'{yellow}\u2022 ',
                u'{blue}\u2022 ',
                u'{green}\u2022 '
            ]
            for diag in params['diagnostics']:
                if disabled and re.match(disabled, diag['message']):
                    continue
                (line0, col0), end = utils.range(diag['range'])
                flags.append(str(line0) + '|' +
                             from_severity[diag.get('severity', 1)])
                client.diagnostics[filetype, buffile][line0].append({
                    'col': col0,
                    'end': end,
                    'message': diag['message']
                })
            # todo: Set for the other buffers too (but they need to be opened)
            msg = 'try %{add-highlighter window/ flag_lines default lsp_flags}\n'
            msg += 'set buffer=' + buffile + ' lsp_flags '
            msg += utils.single_quoted(':'.join(flags))
            pipe(msg)

    @client.handler(force=True)
    def lsp_sync(buffile, filetype):
        """
        Synchronize the current file.

        Makes sure that:
            * the language server is registered at the language server,
            * the language server has an up-to-date view on the buffer
              (even if it is not saved),
            * the options lsp_signature_help_chars and lsp_complete_chars
              are set for the buffer according to what the language server
              suggests. (These are examined at an InsertChar hook.)
        """
        msg = ''
        if buffile not in client.chars_setup and (
                client.sig_help_chars.get(filetype) or client.complete_chars.get(filetype)):
            client.chars_setup.add(buffile)

            def s(opt, chars):
                if chars:
                    m = '\nset buffer='
                    m += buffile
                    m += ' ' + opt
                    m += ' ' + utils.single_quoted(''.join(chars))
                    return m
                else:
                    return ''
            msg += s('lsp_signature_help_chars', client.sig_help_chars.get(filetype))
            msg += s('lsp_complete_chars', client.complete_chars.get(filetype))
        return msg

    @client.handler(hidden=True)
    def lsp_send_did_save(langserver, uri):
        """
        Send textDocument/didSave to server
        """
        langserver.call('textDocument/didSave', {
            'textDocument': {
                'uri': uri
            },
        })()

    @client.handler(hidden=True)
    def lsp_buffer_deleted(filetype, buffile):
        """
        Clear the data for a deleted buffer
        """
        client.client_editing[filetype, buffile] = None
        client.timestamps[(filetype, buffile)] = None

    @client.handler('textDocument/signatureHelp',
             lambda pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos},
             params='0..1', enum=[somewhere])
    def lsp_signature_help(arg1, pos, uri, result):
        """
        Write signature help by the cursor, info or docsclient.
        """
        if not result:
            return
        where = arg1 or 'cursor'
        try:
            active = result['signatures'][result['activeSignature']]
            pn = result['activeParameter']
            func_label = active.get('label', '')
            params = active['parameters']
            label = nice_sig(func_label, params, pn, pos)
        except LookupError:
            try:
                label = pyls_signatureHelp(result, pos)
            except LookupError:
                if not result.get('signatures'):
                    label = ''
                else:
                    label = str(result)
        return info_somewhere(label, pos, where)

    @client.handler('textDocument/completion',
             lambda pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos})
    def lsp_complete(line, column, timestamp, buffile, completers, result):
        """
        Complete at the main cursor.

        Example to force completion at word begin:

        map global insert <a-c> '<a-;>:eval -draft %(exec b; lsp-complete)<ret>'

        The option lsp_completions is prepended to the completers if missing.
        """
        if not result:
            return
        cs = complete_items(result.get('items', []))
        s = utils.single_quoted(libkak.complete(line, column, timestamp, cs))
        setup = ''
        opt = 'option=lsp_completions'
        if opt not in completers:
            # put ourclient as the first completer if not listed
            setup = 'set buffer=' + buffile + ' completers '
            setup += ':'.join([opt] + completers) + '\n'
        return setup + 'set buffer=' + buffile + ' lsp_completions ' + s

    @client.handler(params='0..1', enum=[somewhere])
    def lsp_diagnostics(arg1, timestamp, line, buffile, filetype):
        """
        Describe diagnostics for the cursor line somewhere
        ('cursor', 'info' or 'docsclient'.)

        Hook this to NormalIdle if you want:

        hook -group lsp global NormalIdle .* %{
            lsp-diagnostics cursor
        }
        """
        where = arg1 or 'cursor'
        diag = client.diagnostics.get((filetype, buffile), {})
        if line in diag and diag[line]:
            min_col = 98765
            msgs = []
            for d in diag[line]:
                if d['col'] < min_col:
                    min_col = d['col']
                msgs.append(d['message'])
            pos = {'line': line - 1, 'character': min_col - 1}
            return info_somewhere('\n'.join(msgs), pos, where)

    @client.handler(params='0..2', enum=[('next', 'prev'), somewhere + ['none']])
    def lsp_diagnostics_jump(arg1, arg2, timestamp, line, buffile, filetype, pipe):
        """
        Jump to next or prev diagnostic (relative to the main cursor line)

        Example configuration:

        map global user n ':lsp-diagonstics-jump next cursor<ret>'
        map global user p ':lsp-diagonstics-jump prev cursor<ret>'
        """
        direction = arg1 or 'next'
        where = arg2 or 'none'
        diag = client.diagnostics.get((filetype, buffile))
        if not diag:
            libkak._debug('no diagnostics')
            return
        if timestamp != diag.get('timestamp'):
            pipe('lsp-sync')
        next_line = None
        first_line = None
        last_line = None
        for other_line in six.iterkeys(diag):
            if other_line == 'timestamp':
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
            x = diag[y][0]['col']
            end = diag[y][0]['end']
            msg = libkak.select([((y, x), end)])
            if where == 'none':
                return msg
            else:
                info = client.original['lsp_diagnostics'](arg2, timestamp, y, buffile, filetype)
                return msg + '\n' + (info or '')

    @client.handler('textDocument/hover',
             lambda pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos},
             params='0..1', enum=[somewhere])
    def lsp_hover(arg1, pos, uri, result):
        """
        Display hover information somewhere ('cursor', 'info' or
        'docsclient'.)

        Hook this to NormalIdle if you want:

        hook -group lsp global NormalIdle .* %{
            lsp-hover cursor
        }
        """
        where = arg1 or 'cursor'
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
        return info_somewhere(label, pos, where)

    @client.handler('textDocument/references',
             lambda arg1, pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos,
                 'context': {
                     'includeDeclaration': arg1 != 'false'}},
             params='0..1', enum=[('true', 'false')])
    def lsp_references(arg1, pwd, result):
        """
        Find the references to the identifier at the main cursor.

        Takes one argument, whether to include the declaration or not.
        (default: true)
        """
        m = defaultdict(list)
        for loc in result:
            m[loc['uri']].append(utils.range(loc['range']))
        if m:
            def options():
                for uri, pos in six.iteritems(m):
                    loc = utils.drop_prefix(utils.uri_to_file(uri), pwd).lstrip('/') or uri
                    entry = u'{} ({} references)'.format(loc, len(pos))
                    yield entry, edit_uri_select(uri, pos)
            return libkak.menu(options())
        else:
            return 'echo No results.'

    @client.handler('workspace/executeCommand',
             lambda args: {
                 'command': args[0],
                 'arguments': args[1:]},
             params='1..')
    def lsp_execute_command(args, result):
        """Execute custom command"""
        return 'echo ' + utils.single_quoted(str(result))

    @client.handler('textDocument/definition',
             lambda pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos})
    def lsp_goto_definition(result):
        """
        Go to the definition of the identifier at the main cursor.
        """
        if not result:
            return 'echo -markup {red}No results!'

        if 'uri' in result:
            result = [result]

        if not result:
            return 'echo -markup {red}No results!'

        def options():
            for loc in result:
                p0, p1 = utils.range(loc['range'])
                uri = loc['uri']
                action = edit_uri_select(uri, [(p0, p1)])
                line0, _ = p0
                yield u'{}:{}'.format(uri, line0), action
        return libkak.menu(options())

    @client.handler('textDocument/rename',
             lambda pos, uri, arg1: {
                 'textDocument': {'uri': uri},
                 'position': pos,
                 'newName': arg1 },
             params='1')
    def lsp_rename(result, arg1, pos, uri):
        return apply_workspaceedit(result)

    return client

if __name__ == '__main__':
    makeClient().main(sys.argv[1])
