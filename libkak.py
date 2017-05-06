# -*- coding: utf-8 -*-

from __future__ import print_function
from six.moves.queue import Queue
from subprocess import Popen, PIPE
from threading import Thread
import functools
import inspect
import itertools as it
import os
import re
import six
import sys
import tempfile
import time


def select(cursors):
    """
    >>> print(select([((1,2),(1,4)), ((3,1),(5,72))]))
    1.2,1.4:3.1,5.72
    """
    return ':'.join('%d.%d,%d.%d' % tuple(it.chain(*pos)) for pos in cursors)


def menu(options):
    """
    >>> print(menu([('one', 'echo one'), ('two', 'echo two')]))
    menu -auto-single 'one' 'echo one' 'two' 'echo two'
    """
    opts = join(map(single_quoted, it.chain(*options)))
    return 'menu -auto-single ' + opts


def pipe(session, msg, client=None, sync=False):
    if client:
        msg = u'eval -client {} {}'.format(client, single_quoted(msg))
    if sync:
        fifo, fifo_cleanup = mkfifo()
        msg += u'\n%sh(echo done > {})'.format(fifo)
    p=Popen(['kak', '-p', str(session).rstrip()], stdin=PIPE)
    #print(session, msg, file=sys.stderr)
    p.communicate(encode(msg))
    if sync:
        #print(fifo + ' waiting for line...', file=sys.stderr)
        with open(fifo, 'r') as fifo_fp:
            fifo_fp.readline()
        fifo_cleanup()


def join(words, sep=u' '):
    """
    Join strings or bytes into a string, returning a string.
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


def single_quote_escape(string):
    """
    Backslash-escape ' and \ in Kakoune style .
    """
    return string.replace("\\'", "\\\\'").replace("'", "\\'")


def single_quoted(string):
    u"""
    The string wrapped in single quotes and escaped in Kakoune style.

    https://github.com/mawww/kakoune/issues/1049

    >>> print(single_quoted(u"i'ié"))
    'i\\'ié'
    """
    return u"'" + single_quote_escape(string) + u"'"


def complete(line, column, timestamp, completions):
    u"""
    Format completion options for a Kakoune option.

    >>> print(complete(5, 20, 1234, [
    ...     ('__doc__', 'object’s docstring', '__doc__ (method)'),
    ...     ('||', 'logical or', '|| (func: infix)')
    ... ]))
    5.20@1234:__doc__|object’s docstring|__doc__ (method):\|\||logical or|\|\| (func\: infix)
    """
    rows = (join((backslash_escape('|:', x) for x in c), sep='|')
            for c in completions)
    return u'{}.{}@{}:{}'.format(line, column, timestamp, join(rows, sep=':'))


def backslash_escape(cs, s):
    for c in cs:
        s = s.replace(c, "\\" + c)
    return s


def coord(s):
    return tuple(map(int, s.split('.')))


def selection_desc(x):
    return tuple(map(coord, x.split(',')))


def string(x):
    return x


def listof(p):
    r"""

    >>> import random
    >>> def random_fragment():
    ...     return ''.join(random.sample(':\\abc', random.randrange(1, 5)))
    >>> def test(n):
    ...     xs = [random_fragment() for _ in range(n)]
    ...     if xs and xs[-1] == '':
    ...         xs[-1] = 'c'
    ...     exs = ':'.join(backslash_escape('\\:', s) for s in xs)
    ...     xs2 = listof(string)(exs)
    ...     assert(xs == xs2)
    >>> for n in range(0, 10):
    ...     test(n)

    """

    def inner(s):
        def rmlastcolon(s):
            if s and s[-1] == ':':
                return s[:-1]
            else:
                return s

        ms = [m.group(0) for m in re.finditer(r'(.*?(?<!\\)(\\\\)*:|.+)', s)]
        ms = [m if i == len(ms) - 1 else rmlastcolon(m)
              for i, m in enumerate(ms)]
        return [p(re.sub(r'\\(.)', '\g<1>', x)) for x in ms]
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


def fork(loop=False):
    def decorate(f):
        def target():
            try:
                while True:
                    f()
                    if not loop:
                        break
            except RuntimeError:
                pass
        thread = Thread(target=target)
        thread.daemonize = True
        thread.start()
    return decorate


def safe_kwcall(f, d):
    args = inspect.getargspec(f).args
    return f(**{k: v for k, v in six.iteritems(d) if k in args})


def remote(session,
           oneshot=False,
           oneshot_client=None,
           modsh=None,
           def_quoting=('%(', ')'),
           sh_quoting=('%sh(', ')'),
           quickargs=quickargs,
           more_quickargs={},
           before_decorated=None,
           params='0',
           sync=[]):

    def decorate(f):

        argspecs = inspect.getargspec(f).args
        if before_decorated:
            argspecs += inspect.getargspec(before_decorated).args

        if 'client' not in argspecs:
            argspecs += ['client']

        qa = dict(quickargs, **more_quickargs)
        args = []
        for arg in argspecs:
            if arg in qa:
                splice, parse = qa[arg]
                args.append((arg, splice, parse))

        fifo, fifo_cleanup = mkfifo()

        msg = """__newline="\n"
               __args=""
               for __arg; do __args="${{__args}}_S${{__arg//_/_u}}"; done
               {underscores}
               echo -n "{argsplice}" > {fifo}
            """.format(
                underscores = '\n'.join('__' + splice + '=${' + splice + '//_/_u}'
                                        for _, splice, _ in args),
                argsplice = '_s'.join('${__' + splice + '//$__newline/_n}'
                                      for _, splice, _ in args),
                fifo = fifo)

        if modsh:
            msg = modsh(msg)

        msg = sh_quoting[0] + msg + sh_quoting[1]

        if not oneshot:
            head = "def -allow-override -params {params} -docstring {docstring} {name} ".format(
                name = f.__name__,
                params = params,
                docstring = single_quoted(f.__doc__ or ''))
            msg = head + def_quoting[0] + msg + def_quoting[1]

        if oneshot and oneshot_client:
            head = 'eval -client ' + oneshot_client + ' '
            msg = head + def_quoting[0] + msg + def_quoting[1]

        pipe(session, msg, sync='setup' in sync)

        def listen():
            #print(fifo + ' waiting for line...', file=sys.stderr)
            with open(fifo, 'r') as fp:
                line = decode(fp.readline()).rstrip()
                if line == '_q':
                    fifo_cleanup()
                    raise RuntimeError('fifo demands quit')
                #print(fifo + ' replied:' + repr(line), file=sys.stderr)
                params = [v.replace('_n', '\n').replace('_u', '_')
                          for v in line.split('_s')]
            #print(fifo + ' replied:', params, file=sys.stderr)
            if oneshot:
                fifo_cleanup()
            r = {}
            for arg, value in zip(args, params):
                name, _, parse = arg
                r[name] = parse(value)

            try:
                r['pipe'] = lambda msg: pipe(session, msg, r['client'])
                r['sync_pipe'] = lambda msg: pipe(session, msg, r['client'], sync=True)
                if before_decorated:
                    r['r'] = r  # so that before_decorated may modify it
                    safe_kwcall(before_decorated, r)

                return safe_kwcall(f, r)
                # cannot be synced without queue as it is on a forked thread
            except TypeError as e:
                print(str(e), file=sys.stderr)


        if oneshot:
            if 'oneshot' in sync:
                return listen()
            else:
                fork()(listen)
        else:
            fork(loop=True)(listen)
            @functools.wraps(f)
            def call_from_python(client, *args):
                escaped = [single_quoted(arg) for arg in args]
                pipe(session, ' '.join([f.__name__] + escaped), client,
                     sync='call_from_python' in sync)
            return call_from_python

    return decorate


def mkfifo(active_fifos = {}):
    fifo_dir = tempfile.mkdtemp()
    fifo = os.path.join(fifo_dir, 'fifo')
    os.mkfifo(fifo)
    def rm():
        del active_fifos[fifo]
        os.remove(fifo)
        os.rmdir(fifo_dir)
    active_fifos[fifo] = rm
    return fifo, rm


def fifo_cleanup():
    for x in list(six.iterkeys(mkfifo.__defaults__[0])):
        open(x, 'w').write('_q\n')


def headless(ui='dummy'):
    p = Popen(['kak','-n','-ui',ui])
    time.sleep(0.01)
    return p


def test_remote_commands_sync():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote(kak.pid, sync='setup')
    ... def write_position(line, column, sync_pipe):
    ...      sync_pipe(join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> libkak.pipe(kak.pid, 'write_position', 'unnamed0', sync=True)
    >>> libkak.pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> libkak.pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> print(libkak.remote(kak.pid, oneshot=True,
    ...               oneshot_client='unnamed0', sync='oneshot')(
    ...     lambda selection: selection))
    1:1, 1:5
    >>> q = Queue()
    >>> libkak.remote(kak.pid, oneshot=True,
    ...               oneshot_client='unnamed0')(
    ...     lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> fifo_cleanup()
    """
    pass


def test_unicode_and_escaping():
    u"""
    >>> kak = libkak.headless()
    >>> libkak.pipe(kak.pid, u'exec iapa_bepa<ret>åäö_s_u_n<esc>%H', 'unnamed0')
    >>> call = libkak.remote(kak.pid, oneshot=True, oneshot_client='unnamed0', sync='oneshot')
    >>> print(call(lambda selection: selection))
    apa_bepa
    åäö_s_u_n
    >>> print(call(lambda selection_desc: selection_desc))
    ((1, 1), (2, 12))
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> fifo_cleanup()
    """
    pass


def test_remote_commands_async():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote(kak.pid)
    ... def write_position(pipe, line, column):
    ...      pipe(join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> libkak.pipe(kak.pid, 'write_position', 'unnamed0')
    >>> time.sleep(0.02)
    >>> libkak.pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0')
    >>> write_position('unnamed0')
    >>> time.sleep(0.01)
    >>> libkak.pipe(kak.pid, 'exec \%H', 'unnamed0')
    >>> q = Queue()
    >>> libkak.remote(kak.pid, oneshot=True, oneshot_client='unnamed0')(lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> fifo_cleanup()
    """
    pass


def test_commands_with_params():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote(kak.pid, params='2..', sync='call_from_python')
    ... def test(arg1, arg2, args):
    ...      print(', '.join((arg1, arg2) + args[2:]))
    >>> test(None, 'one', 'two', 'three', 'four')
    one, two, three, four
    >>> test(None, 'a\\nb', 'c_d', 'e_sf', 'g_u_n__ __n_S_s__Sh')
    a
    b, c_d, e_sf, g_u_n__ __n_S_s__Sh
    >>> libkak.pipe(kak.pid, "test 'a\\nb' c_d e_sf 'g_u_n__ __n_S_s__Sh'", sync=True)
    a
    b, c_d, e_sf, g_u_n__ __n_S_s__Sh
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> fifo_cleanup()
    """
    pass


if __name__ == '__main__':
    import doctest
    import sys
    doctest.testmod(extraglobs={'libkak': sys.modules[__name__]})
    sys.exit()


