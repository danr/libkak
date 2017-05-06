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
import utils


class Remote(object):
    def __init__(self, session):
        self.session = session
        self.pre = lambda _: '%sh('
        self.post = ')'
        self.arg_config = {}
        self.sync_setup = False
        self.call_list = []

        def ret():
            x = self.listen()
            self.fifo_cleanup()
            return x
        self.ret = ret

    def _make_async(r):
        r_ret = r.ret
        r.ret = lambda: utils.fork()(r_ret)

    @staticmethod
    def asynchronous(session):
        r = Remote(session)
        r._make_async()
        return r

    @staticmethod
    def onclient(session, client, sync=True):
        r = Remote(session)
        r_pre = r.pre
        r.pre = lambda f: 'eval -client ' + client + ' %(' + r_pre(f)
        r.post = ')' + r.post
        if not sync:
            r._make_async()
        return r

    @staticmethod
    def command(session, params='0', enum=[], sync_setup=False, sync_python_calls=False):
        r = Remote(session)
        r.sync_setup = sync_setup
        def ret():
            utils.fork(loop=True)(r.listen)
            @functools.wraps(r.f)
            def call_from_python(client, *args):
                escaped = [utils.single_quoted(arg) for arg in args]
                pipe(session, ' '.join([r.f.__name__] + escaped), client,
                     sync=sync_python_calls)
            return call_from_python
        r.ret = ret
        r_pre = r.pre
        def pre(f):
            s = 'def -allow-override -params {params} -docstring {docstring} {name}'
            s = s.format(name = f.__name__,
                         params = params,
                         docstring = utils.single_quoted(f.__doc__ or ''))
            if enum:
                sh = "echo '" + '\n'.join(enum) + "'"
                s += ' -shell-candidates %{' + sh + '} '
            s += ' %('
            s += r_pre(f)
            return s
        r.pre = pre
        r.post += ')'
        return r

    def _argnames(self):
        names = set(inspect.getargspec(self.f).args)
        for g in self.call_list:
            names.extend(inspect.getargspec(g).args)

        if 'client' not in names:
            names.add('client')
        return names

    @staticmethod
    def _msg(splices, fifo):
        underscores = []
        argsplice = []
        for s in splices:
            underscores.append('__' + s + '=${' + s + '//_/_u}')
            argsplice.append('${__' + s + '//$__newline/_n}')
        underscores = '\n'.join(underscores)
        argsplice = '_s'.join(argsplice)

        m = ["__newline='\n'"]
        if '__args' in splices:
            m.append('__args=""')
            m.append('for __arg; do __args="${__args}_S${__arg//_/_u}"; done')

        m.append(underscores)
        m.append('echo -n "' + argsplice + '" > ' + fifo)
        return '\n'.join(m)

    def __call__(self, f):
        self.f = f
        splices, self.parse = _argsetup(self._argnames(), self.arg_config)
        self.fifo, self.fifo_cleanup = _mkfifo()
        msg = self.pre(f) + self._msg(splices, self.fifo) + self.post
        pipe(self.session, msg, sync=self.sync_setup)
        return self.ret()

    def listen(self):
        #_debug(self.fifo + ' waiting for line...')
        with open(self.fifo, 'r') as fp:
            line = utils.decode(fp.readline()).rstrip()
            if line == '_q':
                self.fifo_cleanup()
                #_debug(self.fifo, 'demands quit')
                raise RuntimeError('fifo demands quit')
            # _debug(self.fifo + ' replied:' + repr(line))

        r = self.parse(line)

        try:
            r['reply'] = lambda msg: pipe(self.session, msg, r['client'])
            r['reply_sync'] = lambda msg: pipe(self.session, msg, r['client'], sync=True)
            r['r'] = r
            for g in self.call_list:
                _safe_kwcall(g, r)

            return _safe_kwcall(self.f, r)
        except TypeError as e:
            print(str(e), file=sys.stderr)


def pipe(session, msg, client=None, sync=False):
    """
    Send commands to a running Kakoune process.

    If sync is true, this function will return after
    the commands have been executed.

    >>> with tempfile.NamedTemporaryFile() as tmp:
    ...     kak = headless()
    ...     pipe(kak.pid, 'edit ' + tmp.name, 'unnamed0', sync=True)
    ...     pipe(kak.pid, 'exec itest<esc>', 'unnamed0')
    ...     pipe(kak.pid, 'write', 'unnamed0', sync=True)
    ...     print(utils.decode(tmp.read()).rstrip())
    ...     pipe(kak.pid, 'quit', 'unnamed0', sync=True)
    ...     kak.wait()
    test
    0
    """
    if client:
        msg = u'eval -client {} {}'.format(client, utils.single_quoted(msg))
    if sync:
        fifo, fifo_cleanup = _mkfifo()
        msg += u'\n%sh(echo done > {})'.format(fifo)
    p=Popen(['kak', '-p', str(session).rstrip()], stdin=PIPE)
    # _debug(session, msg)
    p.communicate(utils.encode(msg))
    if sync:
        #_debug(fifo + ' waiting for line...')
        with open(fifo, 'r') as fifo_fp:
            fifo_fp.readline()
        fifo_cleanup()
        #_debug(fifo + ' done')


#############################################################################
# Kakoune commands


def select(cursors):
    """
    A command to select some cursors.

    >>> print(select([((1,2),(1,4)), ((3,1),(5,72))]))
    select 1.2,1.4:3.1,5.72
    """
    return 'select ' + ':'.join('%d.%d,%d.%d' % tuple(it.chain(*pos))
                               for pos in cursors)


def menu(options):
    """
    A command to make a menu.

    Takes a list of 2-tuples of an entry and the command it executes.

    >>> print(menu([('one', 'echo one'), ('two', 'echo two')]))
    menu -auto-single 'one' 'echo one' 'two' 'echo two'
    """
    opts = utils.join(map(utils.single_quoted, it.chain(*options)))
    return 'menu -auto-single ' + opts


def complete(line, column, timestamp, completions):
    u"""
    Format completion for a Kakoune option.

    >>> print(complete(5, 20, 1234, [
    ...     ('__doc__', 'object’s docstring', '__doc__ (method)'),
    ...     ('||', 'logical or', '|| (func: infix)')
    ... ]))
    5.20@1234:__doc__|object’s docstring|__doc__ (method):\|\||logical or|\|\| (func\: infix)
    """
    rows = (utils.join((utils.backslash_escape('|:', x) for x in c), sep='|')
            for c in completions)
    return u'{}.{}@{}:{}'.format(line, column, timestamp, utils.join(rows, sep=':'))


#############################################################################
# Arguments and argument parsers


def coord(s):
    """
    Parse a Kakoune coordinate.
    """
    return tuple(map(int, s.split('.')))


def selection_desc(x):
    """
    Parse a Kakoune selection description.
    """
    return tuple(map(coord, x.split(',')))


def string(x):
    """
    Parse a Kakoune string.
    """
    return x


def listof(p):
    r"""
    Parse a Kakoune list of p.

    >>> import random
    >>> def random_fragment():
    ...     return ''.join(random.sample(':\\abc', random.randrange(1, 5)))
    >>> def test(n):
    ...     xs = [random_fragment() for _ in range(n)]
    ...     if xs and xs[-1] == '':
    ...         xs[-1] = 'c'
    ...     exs = ':'.join(utils.backslash_escape('\\:', s) for s in xs)
    ...     xs2 = listof(string)(exs)
    ...     assert(xs == xs2)
    >>> for n in range(0, 10):
    ...     test(n)

    """
    def rmlastcolon(s):
        if s and s[-1] == ':':
            return s[:-1]
        else:
            return s

    def inner(s):
        ms = [m.group(0) for m in re.finditer(r'(.*?(?<!\\)(\\\\)*:|.+)', s)]
        ms = [m if i == len(ms) - 1 else rmlastcolon(m)
              for i, m in enumerate(ms)]
        return [p(re.sub(r'\\(.)', '\g<1>', x)) for x in ms]
    return inner


def boolean(s):
    """
    Parse a Kakoune boolean.
    """
    return s == 'true'


def _args_parse(s):
    return tuple(x.replace('_u', '_') for x in s.split('_S')[1:])


arg_config = {
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

    'args': ('__args', _args_parse),
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


def _argsetup(argnames, config):
    args = []
    splices = []
    for name in argnames:
        try:
            if name in config:
                splice, parse = config[name]
            else:
                splice, parse = arg_config[name]
            splices.append(splice)
            args.append((name, parse))
        except KeyError:
            pass
    def parse(line):
        params = [v.replace('_n', '\n').replace('_u', '_')
                  for v in line.split('_s')]
        return {name: parse(value)
                for (name, parse), value in zip(args, params)}
    return splices, parse


#############################################################################
# Private utils


def _safe_kwcall(f, d):
    args = inspect.getargspec(f).args
    return f(**{k: v for k, v in six.iteritems(d) if k in args})


def _mkfifo(active_fifos = {}):
    """
    Return a pair of a new fifo' filename and a cleanup function.
    """
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
    """
    Writes _q to all open fifos created by _mkfifo.
    """
    for x in list(six.iterkeys(_mkfifo.__defaults__[0])):
        with open(x, 'w') as fd:
            fd.write('_q\n')
            fd.flush()


def _debug(*xs):
    print(*xs, file=sys.stderr)


#############################################################################
# Tests


def headless(ui='dummy'):
    """
    Start a headless Kakoune process.
    """
    p = Popen(['kak','-n','-ui',ui])
    time.sleep(0.01)
    return p


def test_remote_commands_sync():
    u"""
    >>> kak = headless()
    >>> @Remote.command(kak.pid, sync_setup=True)
    ... def write_position(line, column, reply_sync):
    ...      reply_sync(utils.join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> pipe(kak.pid, 'write_position', 'unnamed0', sync=True)
    >>> pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> print(Remote.onclient(kak.pid, 'unnamed0')(
    ...     lambda selection: selection))
    1:1, 1:5
    >>> q = Queue()
    >>> Remote.onclient(kak.pid, 'unnamed0', sync=False)(
    ...     lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_unicode_and_escaping():
    u"""
    >>> kak = headless()
    >>> pipe(kak.pid, u'exec iapa_bepa<ret>åäö_s_u_n<esc>%H', 'unnamed0')
    >>> call = Remote.onclient(kak.pid, 'unnamed0')
    >>> print(call(lambda selection: selection))
    apa_bepa
    åäö_s_u_n
    >>> print(call(lambda selection_desc: selection_desc))
    ((1, 1), (2, 12))
    >>> pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_remote_commands_async():
    u"""
    >>> kak = headless()
    >>> @Remote.command(kak.pid)
    ... def write_position(reply, line, column):
    ...      reply(utils.join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> pipe(kak.pid, 'write_position', 'unnamed0')
    >>> time.sleep(0.02)
    >>> pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> time.sleep(0.01)
    >>> pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> Remote.onclient(kak.pid, 'unnamed0')(lambda selection: print(selection))
    1:1, 1:5
    >>> q = Queue()
    >>> Remote.onclient(kak.pid, 'unnamed0', sync=False)(lambda selection: q.put(selection))
    >>> print(q.get())
    1:1, 1:5
    >>> pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


def test_commands_with_params():
    u"""
    >>> kak = headless()
    >>> @Remote.command(kak.pid, params='2..', sync_python_calls=True)
    ... def test(arg1, arg2, args):
    ...      print(', '.join((arg1, arg2) + args[2:]))
    >>> test(None, 'one', 'two', 'three', 'four')
    one, two, three, four
    >>> test(None, 'a\\nb', 'c_d', 'e_sf', 'g_u_n__ __n_S_s__Sh')
    a
    b, c_d, e_sf, g_u_n__ __n_S_s__Sh
    >>> pipe(kak.pid, "test 'a\\nb' c_d e_sf 'g_u_n__ __n_S_s__Sh'", sync=True)
    a
    b, c_d, e_sf, g_u_n__ __n_S_s__Sh
    >>> pipe(kak.pid, 'quit!', 'unnamed0')
    >>> kak.wait()
    0
    >>> _fifo_cleanup()
    """
    pass


#############################################################################
# Main


if __name__ == '__main__':
    import doctest
    doctest.testmod()


