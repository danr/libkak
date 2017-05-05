# -*- coding: utf-8 -*-
"""

>>> kak = libkak.headless()
>>> @libkak.remote(kak.pid)
... def write_position(line, column):
...      return join('exec', 'a', str(y), ':', str(x), '<esc>')
>>> libkak.pipe(kak.pid, 'write_position')
>>> libkak.pipe('a,<space><esc>')
>>> write_position(kak)
>>> kak.execute('xH')
>>> libkak.remote(kak.pid)(lambda selection: print(selection))
1:1, 1:5
>>> libkak.pipe(kak.pid, 'q!')

"""
from __future__ import print_function
import inspect
import sys
import os
import tempfile
from functools import wraps
from subprocess import Popen, PIPE
from threading import Thread, Queue
import itertools as it
import threading
import time
import shutil
import six
import re
import tempfile


def join(words, sep=u' '):
    """
    Join strings or bytes into a string.
    """
    return decode(sep).join(decode(w) for w in words)


def encode(s):
    """
    Encode a unicode string into bytes.
    """
    if isinstance(s, six.binary_type):
        return s
    elif isinstance(s, six.string_types):
        return s.encode('utf-8')
    else:
        raise ValueError('Expected string or bytes')


def decode(s):
    """
    Decode into a string (a unicode object).
    """
    if isinstance(s, six.binary_type):
        return s.decode('utf-8')
    elif isinstance(s, six.string_types):
        return s
    else:
        raise ValueError('Expected string or bytes')


def headless(debug=False, ui='dummy'):
    proc = Popen(['kak','-n','-ui',ui])
    time.sleep(0.01)
    kak = Kak('pipe', proc.pid, 'unnamed0', debug=debug)
    kak._pid = proc.pid
    kak.sync()
    return kak


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


def coord(s):
    return tuple(map(int, s.split('.')))

def selection_desc(x):
    return tuple(map(coord, x.split(',')))

def string(x):
    return x

def listof(p):
    def inner(s):
        m = list(re.split(r'(?<!\\)(\\\\)*:', s))
        ms = [x+(y or '') for x, y in zip(m[::2], (m+[''])[1::2])]
        return [p(x.replace('\\\\', '\\').replace('\\:', ':'))
                for x in ms]
    return inner

def args_parse(s):
    return tuple(x.replace('_u', '_') for x in s.split('_S')[1:])


def boolean(s):
    return s == 'true'


quickargs = {
    'line':   ('kak_cursor_line',   int),
    'column': ('kak_cursor_column', int),

    'aligntab':    ('kak_opt_aligntab',    boolean),
    'filetype':    ('kak_opt_filetype',    string),
    'indentwidth': ('kak_opt_indentwidth', int),
    'readonly':    ('kak_opt_readonly',    boolean),
    'readonly':    ('kak_opt_readonly',    boolean),
    'tabstop':     ('kak_opt_tabstop',     int),

    'pwd':  ('PWD',  string),
    'PWD':  ('PWD',  string),
    'PATH': ('PATH', string),
    'HOME': ('HOME', string),

    'args': ('__args', args_parse),
    'arg1': ('1',      string),
    'arg2': ('2',      string),
    'arg3': ('3',      string),
    'arg4': ('4',      string),
    'arg5': ('5',      string),
    'arg6': ('6',      string),
    'arg7': ('7',      string),
    'arg8': ('8',      string),
    'arg9': ('9',      string),

    'bufname':            ('kak_bufname',            string),
    'buffile':            ('kak_buffile',            string),
    'buflist':            ('kak_buflist',            listof(string)),
    'timestamp':          ('kak_timestamp',          int),
    'selection':          ('kak_selection',          string),
    'selections':         ('kak_selections',         listof(string)),
    'runtime':            ('kak_runtime',            string),
    'session':            ('kak_session',            string),
    'client':             ('kak_client',             string),
    'cursor_line':        ('kak_cursor_line',        int),
    'cursor_column':      ('kak_cursor_column',      int),
    'cursor_char_column': ('kak_cursor_char_column', int),
    'cursor_byte_offset': ('kak_cursor_byte_offset', int),
    'selection_desc':     ('kak_selection_desc',     selection_desc),
    'selections_desc':    ('kak_selections_desc',    listof(selection_desc)),
    'window_width':       ('kak_window_width',       int),
    'window_height':      ('kak_window_height',      int),
}


def fork(loop):
    def decorate(f):
        def target():
            while True:
                f()
                if not loop:
                    break
        thread = Thread(target=target)
        thread.start()
    return decorate


def remote(session,
           oneshot=False,
           modsh=None,
           def_quoting=('%(', ')'),
           sh_quoting=('%sh(', ')'),
           quickargs=quickargs,
           more_quickargs={},
           before_decorated=None,
           params='0'):

    def decorate(f):

        import inspect

        argspecs = inspect.getargspec(f).args
        if before_decorated:
            argspecs += inspect.getargspec(before_decorated).args

        qa = dict(quickargs, more_quickargs)
        args = []
        for arg in argspecs:
            if arg != 'r':
                splice, parse = qa[arg]
                args.append((arg, splice, parse))

        fifo = TODO

        msg = r"""
               __args=""
               for __arg; do __args="${{__args}}_S${{__arg//_/_u}}" done
               __argsplice="{argsplice}"
               echo "${{__argsplice//\n/_n}}" > {fifo}
            """.format(
                argsplice = '_s'.join('${' + splice + '//_/_u}'
                                      for _, splice, _ in args),
                fifo = fifo)

        if modsh:
            msg = modsh(msg)

        msg = sh_quoting[0] + msg + sh_quoting[1]

        if not oneshot:
            head = "def -allow-override -params {params} -docstring {docstring} {name} ".format(
                name = f.__name__,
                params = params,
                docstring = single_quoted(f.__docstring__))
            msg = head + def_quoting[0] + msg + def_quoting[1]

        pipe_to_kak(session, msg)

        @fork(loop = not oneshot)
        def listen():
            with open(fifo, 'r') as fp:
                params = [v.replace('_n', '\n').replace('_u', '_')
                          for v in fp.readline().split('_s')]
            r = {}
            for arg, value in it.izip_longest(args, params)
                name, _, parse = arg
                r[name] = parse(value)

            if before_decorated:
                r['r'] = r  # so that before_decorated may modify it
                before_decorated(**r)

            x = f(**r)
            if x:
                pipe_to_kak(session, x)


        if oneshot:
            return None
        else:
            import functools
            @functools.wraps(f)
            def call_from_python(*args):
                escaped = [single_quoted(arg) for arg in args]
                pipe_to_kak(' '.join([f.__name__] + escaped))
            return call_from_python

    return decorate




def _mkfifo(kak):
    kak._counter += 1
    name = kak._dir + '/' + str(kak._counter)
    os.mkfifo(name)
    return name


def _cmd_test():
    """
    >>> kak = headless()
    >>> @kak.cmd()
    ... def test(ctx, txt, y=kak.val.cursor_line):
    ...     ctx.execute("oTest!<space>", txt, "<space>", str(y), "<esc>")
    ...     return ctx.val.selection()
    >>> print(test(kak, 'a'))
    1
    >>> kak.evaluate('test b')
    >>> print(test(kak, 'c'))
    3
    >>> kak.execute("%")
    >>> print(kak.val.selection())
    <BLANKLINE>
    Test! a 1
    Test! b 2
    Test! c 3
    <BLANKLINE>
    >>> kak.quit()
    """
    pass


def _unicode_test():
    u"""
    >>> kak = libkak.headless()
    >>> kak.execute(u"iåäö<esc>Gh")
    >>> print(kak.val.selection())
    åäö
    >>> kak.quit()

    >>> kak = libkak.unconnected()
    >>> kak.execute(u"iåäö<esc>")
    >>> print(kak.debug_sent())
    exec  'iåäö<esc>'
    """
    pass


def _newline_test():
    """
    >>> kak = libkak.headless()
    >>> kak.execute("3o<c-r>#<esc>%")
    >>> print(kak.val.selection())
    <BLANKLINE>
    1
    2
    3
    <BLANKLINE>
    >>> kak.quit()
    """
    pass



def __z_test():
    """
    >>> kak = libkak.headless()
    >>> kak.execute('Zz')
    >>> kak.sync()
    >>> kak.quit()
    """


def lines(s):
    out = ['']
    for c in s:
        if c == '\n':
            out.append('')
        else:
            out[-1] = out[-1]+c
    return out


def _test_selections(fragments, stride=1):
    r"""
    >>> import random
    >>> def random_fragment():
    ...     return ''.join(random.choice(':_su\'')
    ...                    for _ in range(0, 6))
    >>> for n in range(1, 20):
    ...     _test_selections(random_fragment() for _ in range(n))
    """
    descs = []
    buf = ""
    fragments = list(fragments)
    for s in fragments:
        p0 = len(lines(buf)), len(lines(buf)[-1])+1
        buf += s
        p1 = len(lines(buf)), len(lines(buf)[-1])
        y, x = p1
        if x == 0:
            p1 = y-1, len(lines(buf)[-1])+1

        #print(repr(s), p0, p1)
        descs += [(p0, p1)]
    kak = headless()
    with tempfile.NamedTemporaryFile('wb') as f:
        f.write(encode(buf))
        f.flush()
        kak.evaluate('edit ' + f.name)
        kak.select(descs[::stride])
        have = kak.val.selections()
        want = [w for w in fragments[::stride]]
        if have != want:
            print('have, want: ')
            print(have)
            print(want)
            from pprint import pprint
            pprint(list(zip(have, want)))
            print(have == want)
        kak.quit()


if __name__ == '__main__':
    import doctest
    import sys
    doctest.testmod(extraglobs={'libkak': sys.modules[__name__]})
    sys.exit()
    dt_runner = doctest.DebugRunner()
    tests = doctest.DocTestFinder().find(sys.modules[__name__])
    for t in tests:
        t.globs['libkak']=sys.modules[__name__]
        try:
            dt_runner.run(t)
        except doctest.UnexpectedException as e:
            import pdb
            pdb.post_mortem(e.exc_info[2])


