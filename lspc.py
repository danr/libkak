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


def format_pos(pos):
    """
    >>> format_pos({'line': 5, 'character': 0})
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
            format_pos(pos), utils.single_quoted(msg))
    elif where == 'info':
        return 'info ' + utils.single_quoted(msg)
    elif where == 'docsclient':
        tmp = tempfile.mktemp()
        open(tmp, 'wb').write(utils.encode(msg))
        return """
            eval -try-client %opt[docsclient] %[
              edit! -scratch '*doc*'
              exec \%d|cat<space> {tmp}<ret>
              exec \%|fmt<space> - %val[window_width] <space> -s <ret>
              exec gg
              set buffer filetype rst
              try %[rmhl number_lines]
              %sh[rm {tmp}]
            ]""".format(tmp=tmp)


def complete_item(item):
    # item['kind'] is ignored for now
    return (
        item['label'],
        '{}\n\n{}'.format(item.get('detail', ''),
                          item.get('documentation', '')[:500]),
        item['label']
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


def main(session, mock={}):

    langservers = {}
    timestamps = {}

    def make_sync(method, make_params):

        def sync(d, line, column, buffile, filetype, timestamp, pwd, cmd, client, reply):

            d['pos'] = {'line': line - 1, 'character': column - 1}
            d['uri'] = uri = 'file://' + buffile

            if cmd in langservers:
                print(filetype + ' already spawned')
            else:
                langservers[cmd] = Langserver(
                    filetype, session, pwd, cmd, mock)

            d['langserver'] = langserver = langservers[cmd]

            q = Queue()

            old_timestamp = timestamps.get((filetype, buffile))
            if old_timestamp == timestamp:
                print('no need to send update')
                reply('')
            else:
                timestamps[(filetype, buffile)] = timestamp
                with tempfile.NamedTemporaryFile() as tmp:
                    write = "eval -no-hooks 'write {}'".format(tmp.name)
                    libkak.pipe(reply, write, client=client, sync=True)
                    print('finished writing to tempfile')
                    contents = open(tmp.name, 'r').read()
                langserver.client_editing[buffile] = client
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
                print('sync: waiting for didChange reply...')
                q.get()
                print('sync: got didChange reply...')

            if method:
                print(method, 'calling langserver')
                langserver.call(method, utils.safe_kwcall(
                    make_params, d))(q.put)
                return q.get()
            else:
                return {'result': None}

        return sync

    original = {}

    def handler(method=None, make_params=None, params='0', enum=None):
        def decorate(f):
            original[f.__name__] = f

            r = libkak.Remote(session)
            r.command(r, params=params, enum=enum, sync_setup=True)
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
            sync = make_sync(method, make_params)
            r.puns = False
            r.argnames = utils.argnames(sync) + utils.argnames(f)

            @functools.wraps(f)
            def k(d):
                try:
                    d['d'] = d
                    # print('handler calls sync', pprint.pformat(d))
                    msg = utils.safe_kwcall(sync, d)
                    # print('sync called', status, result, pprint.pformat(d))
                    if 'result' in msg:
                        d['result'] = msg['result']
                        print('Calling', f.__name__, pprint.pformat(d)[:500])
                        msg = utils.safe_kwcall(f, d)
                        if msg:
                            d['pipe'](msg)
                    else:
                        print('Error: ', msg)
                        d['pipe']('''
                        echo -debug When handling {}:
                        echo -debug {}
                        echo -color red "Error from language server (see *debug* buffer)"
                        '''.format(utils.single_quoted(f.__name__),
                                   utils.single_quoted(pprint.pformat(msg))))
                except:
                    import traceback
                    msg = f.__name__ + ' ' + traceback.format_exc()
                    print(msg)
                    d['pipe']('''
                    echo -debug When handling {}:
                    echo -debug {}
                    echo -color red "Error from language client (see *debug* buffer)"
                    '''.format(utils.single_quoted(f.__name__),
                               utils.single_quoted(pprint.pformat(msg))))

            return r(k)
        return decorate

    chars_setup = set()

    @handler()
    def lsp_sync(buffile, langserver):
        """
        Synchronize the current file.

        Makes sure that:
            * the language server is registered at the language server,
            * the language server has an up-to-date view on the buffer
              (even if it is not saved),
            * the options lsp_signature_help_chars and lsp_complete_chars
              are set for the buffer according to what the language server
              suggests. (These are examined at an InsertChar hook.)

        Hooked automatically to WinDisplay and filetype WinSetOption.
        """
        msg = 'echo synced'
        if buffile not in chars_setup:
            chars_setup.add(buffile)

            def s(opt, chars):
                if chars:
                    m = '\nset buffer='
                    m += buffile
                    m += ' ' + opt
                    m += ' ' + utils.single_quoted(''.join(chars))
                    return m
                else:
                    return ''
            msg += s('lsp_signature_help_chars', langserver.sig_help_chars)
            msg += s('lsp_complete_chars', langserver.complete_chars)
        return msg

    @handler('textDocument/signatureHelp',
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

    @handler('textDocument/completion',
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
        cs = map(complete_item, result.get('items', []))
        s = utils.single_quoted(libkak.complete(line, column, timestamp, cs))
        setup = ''
        opt = 'option=lsp_completions'
        if opt not in completers:
            # put ourself as the first completer if not listed
            setup = 'set buffer=' + buffile + ' completers '
            setup += ':'.join([opt] + completers) + '\n'
        return setup + 'set buffer=' + buffile + ' lsp_completions ' + s

    @handler(params='0..1', enum=[somewhere])
    def lsp_diagnostics(arg1, timestamp, line, buffile, langserver):
        """
        Describe diagnostics for the cursor line somewhere
        ('cursor', 'info' or 'docsclient'.)

        Hook this to NormalIdle if you want:

        hook -group lsp global NormalIdle .* %{
            lsp-diagnostics cursor
        }
        """
        where = arg1 or 'cursor'
        diag = langserver.diagnostics[buffile]
        if line in diag and diag[line]:
            min_col = 98765
            msgs = []
            for d in diag[line]:
                if d['col'] < min_col:
                    min_col = d['col']
                msgs.append(d['message'])
            pos = {'line': line - 1, 'character': min_col - 1}
            return info_somewhere('\n'.join(msgs), pos, where)

    @handler(params='0..2', enum=[('next', 'prev'), somewhere + ['none']])
    def lsp_diagnostics_jump(arg1, arg2, timestamp, line, buffile, langserver, pipe):
        """
        Jump to next or prev diagnostic (relative to the main cursor line)

        Example configuration:

        map global user n ':lsp-diagonstics-jump next cursor<ret>'
        map global user p ':lsp-diagonstics-jump prev cursor<ret>'
        """
        direction = arg1 or 'next'
        where = arg2 or 'none'
        diag = langserver.diagnostics[buffile]
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
            msg = libkak.select([((y, x), (y, x))])
            if where == 'none':
                return where
            else:
                info = original['lsp_diagnostics'](arg2, timestamp, y, buffile, langserver)
                return msg + '\n' + (info or '')

    @handler('textDocument/hover',
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

    @handler('textDocument/references',
             lambda arg1, pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos,
                 'context': {
                     'includeDeclaration': arg1 != 'false'}},
             params='0..1', enum=[('true', 'false')])
    def lsp_references(arg1, uri, result):
        """
        Find the references to the identifier at the main cursor.

        Takes one argument, whether to include the declaration or not.
        (default: true)
        """
        c = []
        other = 0
        for loc in result:
            if loc['uri'] == uri:
                c.append(utils.range(loc['range']))
            else:
                other += 1
        if c:
            msg = libkak.select(c)
            if other:
                msg += '\necho Also at {} positions in other files'.format(other)
            return msg
        else:
            print('got no results', result)

    @handler('textDocument/definition',
             lambda pos, uri: {
                 'textDocument': {'uri': uri},
                 'position': pos})
    def lsp_goto_definition(result):
        """
        Go to the definition of the identifier at the main cursor.
        """
        if 'uri' in result:
            result = [result]

        if not result:
            return 'echo -color red No results!'

        c = []
        for loc in result:
            p = utils.range(loc['range'])
            c.append((loc['uri'], p))

        options = []
        for uri, (p0, p1) in c:
            if uri.startswith('file://'):
                uri = uri[len('file://'):]
                action = 'edit {}; {}'.format(uri, libkak.select([(p0, p1)]))
            else:
                action = 'echo -color red Cannot open {}'.format(uri)
            line0, _ = p0
            options.append((u'{}:{}'.format(uri, line0), action))
            return libkak.menu(options)

    libkak.pipe(session, """#kak
    remove-hooks global lsp
    try %{declare-option str lsp_servers}
    try %{declare-option str lsp_complete_chars}
    try %{declare-option str lsp_signature_help_chars}
    try %{declare-option completions lsp_completions}
    try %{declare-option line-flags lsp_flags}

    hook -group lsp global WinDisplay .* %{
        try %{add-highlighter flag_lines default lsp_flags}
    }

    hook -group lsp global InsertChar .* %{
        try %{
            exec -draft <esc><space>h<a-k>[ %opt{lsp_complete_chars} ]<ret>
            lsp-complete
        }
        try %{
            exec -draft <esc><space>h<a-k>[ %opt{lsp_signature_help_chars} ]<ret>
            lsp-signature-help
        }
    }

    hook -group lsp global WinSetOption filetype=.* lsp-sync
    hook -group lsp global WinDisplay .* lsp-sync
    """)


if __name__ == '__main__':
    main(int(sys.argv[1]))
