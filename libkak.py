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


def debug(*xs, **kws):
    print(*xs, **kws, file=sys.stderr)


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
    #debug(session, msg)
    p.communicate(encode(msg))
    if sync:
        #debug(fifo + ' waiting for line...')
        with open(fifo, 'r') as fifo_fp:
            fifo_fp.readline()
        fifo_cleanup()
        #debug(fifo + ' done')


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


def _argsetup(argnames, qa):
    args = []
    splices = []
    for name in argnames:
        if name in qa:
            splice, parse = qa[name]
            splices.append(splice)
            args.append((name, parse))
    def parse(line):
        params = [v.replace('_n', '\n').replace('_u', '_')
                  for v in line.split('_s')]
        return {name: parse(value)
                for (name, parse), value in zip(args, params)}
    return splices, parse


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


def remote_def(session, params='0', enum=[], sync_python_calls=False, sync_setup=False):
    r = remote(session, sync_setup=sync_setup)
    def ret():
        fork(loop=True)(r.listen)
        @functools.wraps(r.f)
        def call_from_python(client, *args):
            escaped = [single_quoted(arg) for arg in args]
            pipe(session, ' '.join([r.f.__name__] + escaped), client,
                 sync=sync_python_calls)
        return call_from_python
    r.ret = ret
    r_pre = r.pre
    def pre(f):
        s = 'def -allow-override -params {params} -docstring {docstring} {name}'
        s = s.format(name = f.__name__,
                     params = params,
                     docstring = single_quoted(f.__doc__ or ''))
        if enum:
            sh = "echo '" + '\n'.join(enum) + "'"
            s += ' -shell-candidates %{' + sh + '} '
        s += ' %('
        s += r_pre(f)
        return s
    r.pre = pre
    r.post += ')'
    return r


def _remote_msg(fifo, splices):
    underscores = []
    argsplice = []
    for s in splices:
        underscores.append('__' + s + '=${' + s + '//_/_u}')
        argsplice.append('${__' + s + '//$__newline/_n}')
    underscores = '\n'.join(underscores)
    argsplice = '_s'.join(argsplice)

    return """__newline="\n"
           __args=""
           for __arg; do __args="${{__args}}_S${{__arg//_/_u}}"; done
           {underscores}
           echo -n "{argsplice}" > {fifo}
        """.format(fifo=fifo, underscores=underscores, argsplice=argsplice)


class remote(object):
    def __init__(self, session, sync_setup=False):
        self.session     = session
        self.pre = lambda _: '%sh('
        self.post = ')'
        self.quickargs = quickargs.copy()
        self.sync_setup = sync_setup
        self.call_list = []

        def ret():
            x = self.listen()
            self.fifo_cleanup()
            return x
        self.ret = ret

    def asynchronous(r):
        r_ret = r.ret
        r.ret = lambda: fork()(r_ret)
        return r

    def onclient(r, client=''):
        r_pre = r.pre
        r.pre = lambda f: 'eval -client ' + client + ' %(' + r_pre(f)
        r.post = ')' + r.post
        return r

    def argnames(self):
        names = set(inspect.getargspec(self.f).args)
        for g in self.call_list:
            names.extend(inspect.getargspec(g).args)

        if 'client' not in names:
            names.add('client')
        return names

    def __call__(self, f):
        self.f = f

        splices, self.parse = _argsetup(self.argnames(), self.quickargs)

        self.fifo, self.fifo_cleanup = mkfifo()

        msg = self.pre(f) + _remote_msg(self.fifo, splices) + self.post

        pipe(self.session, msg, sync=self.sync_setup)

        return self.ret()

    def listen(self):
        #debug(self.fifo + ' waiting for line...')
        with open(self.fifo, 'r') as fp:
            line = decode(fp.readline()).rstrip()
            if line == '_q':
                self.fifo_cleanup()
                #debug(self.fifo, 'demands quit')
                raise RuntimeError('fifo demands quit')
            #debug(self.fifo + ' replied:' + repr(line))

        r = self.parse(line)

        try:
            r['reply'] = lambda msg: pipe(self.session, msg, r['client'])
            r['reply_sync'] = lambda msg: pipe(self.session, msg, r['client'], sync=True)
            r['r'] = r
            for g in self.call_list:
                safe_kwcall(g, r)

            return safe_kwcall(self.f, r)
        except TypeError as e:
            print(str(e), file=sys.stderr)


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


def _fifo_cleanup():
    for x in list(six.iterkeys(mkfifo.__defaults__[0])):
        with open(x, 'w') as fd:
            fd.write('_q\n')
            fd.flush()

def headless(ui='dummy'):
    p = Popen(['kak','-n','-ui',ui])
    time.sleep(0.01)
    return p


def test_remote_commands_sync():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote_def(kak.pid, sync_setup=True)
    ... def write_position(line, column, reply_sync):
    ...      reply_sync(join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> libkak.pipe(kak.pid, 'write_position', 'unnamed0', sync=True)
    >>> libkak.pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> libkak.pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> print(libkak.remote(kak.pid).onclient('unnamed0')(
    ...     lambda selection: selection))
    1:1, 1:5
    >>> q = Queue()
    >>> libkak.remote(kak.pid).onclient('unnamed0').asynchronous()(
    ...     lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_unicode_and_escaping():
    u"""
    >>> kak = libkak.headless()
    >>> libkak.pipe(kak.pid, u'exec iapa_bepa<ret>åäö_s_u_n<esc>%H', 'unnamed0')
    >>> call = libkak.remote(kak.pid).onclient('unnamed0')
    >>> print(call(lambda selection: selection))
    apa_bepa
    åäö_s_u_n
    >>> print(call(lambda selection_desc: selection_desc))
    ((1, 1), (2, 12))
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_remote_commands_async():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote_def(kak.pid)
    ... def write_position(reply, line, column):
    ...      reply(join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> libkak.pipe(kak.pid, 'write_position', 'unnamed0')
    >>> time.sleep(0.02)
    >>> libkak.pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> time.sleep(0.01)
    >>> libkak.pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> libkak.remote(kak.pid).onclient('unnamed0')(lambda selection: print(selection))
    1:1, 1:5
    >>> q = Queue()
    >>> libkak.remote(kak.pid).onclient('unnamed0').asynchronous()(lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> libkak.pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_commands_with_params():
    u"""
    >>> kak = libkak.headless()
    >>> @libkak.remote_def(kak.pid, params='2..', sync_python_calls=True)
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
    >>> _fifo_cleanup()
    """
    pass


if __name__ == '__main__':
    import doctest
    import sys
    doctest.testmod(extraglobs={'libkak': sys.modules[__name__]})
    sys.exit()


