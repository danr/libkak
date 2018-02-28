# -*- coding: utf-8 -*-

from __future__ import print_function
from six.moves.queue import Queue
from subprocess import Popen, PIPE
from threading import Thread
import functools
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
        self.puns = True
        self.argnames = []
        self.sync_setup = False
        self.required_names = {'client'}

        def ret():
            x = self.listen()
            self.fifo_cleanup()
            return x
        self.ret = ret

    @staticmethod
    def _resolve(self_or_session):
        if isinstance(self_or_session, Remote):
            return self_or_session
        else:
            return Remote(self_or_session)

    @staticmethod
    def setup_reply_channel(self_or_session):
        r = Remote._resolve(self_or_session)
        r_pre = r.pre
        r.pre = lambda f: r_pre(f) + '''
                    __reply_fifo_dir=$(mktemp -d)
                    __reply_fifo="${__reply_fifo_dir}/fifo"
                    mkfifo ${__reply_fifo}
                '''
        r.post = '''
            \ncat ${__reply_fifo}
            rm ${__reply_fifo}
            rmdir ${__reply_fifo_dir}
        ''' + r.post
        r.arg_config['reply_fifo'] = ('__reply_fifo', Args.string)
        r.required_names.add('reply_fifo')
        return r

    @staticmethod
    def asynchronous(self_or_session):
        r = Remote._resolve(self_or_session)
        r_ret = r.ret
        r.ret = lambda: utils.fork()(r_ret)
        return r

    @staticmethod
    def onclient(self_or_session, client, sync=True):
        r = Remote._resolve(self_or_session)
        r_pre = r.pre
        r.pre = lambda f: 'eval -client ' + client + ' %(' + r_pre(f)
        r.post = ')' + r.post
        if not sync:
            r.asynchronous(r)
        return r

    @staticmethod
    def hook(self_or_session, scope, name, group=None, filter='.*',
             sync_setup=False, client=None):
        r = Remote._resolve(self_or_session)
        r.sync_setup = sync_setup
        group = ' -group ' + group if group else ''
        filter = utils.single_quoted(filter)
        cmd = 'hook' + group + ' ' + scope + ' ' + name + ' ' + filter + ' %('
        r_pre = r.pre
        r.pre = lambda f: cmd + r_pre(f)
        r.post = ')' + r.post
        r.ret = lambda: utils.fork(loop=True)(r.listen)
        if client:
            r.onclient(r, client)
        return r

    def _f_name(self):
        return self.f.__name__.replace('_', '-')

    @staticmethod
    def command(self_or_session, params='0', enum=[],
                sync_setup=False, sync_python_calls=False, hidden=False):
        r = Remote._resolve(self_or_session)
        r.sync_setup = sync_setup

        def ret():
            utils.fork(loop=True)(r.listen)

            @functools.wraps(r.f)
            def call_from_python(client, *args):
                escaped = [utils.single_quoted(arg) for arg in args]
                pipe(r.session, ' '.join([r._f_name()] + escaped), client,
                     sync=sync_python_calls)
            return call_from_python
        r.ret = ret
        r_pre = r.pre

        def pre(f):
            s = 'def -allow-override -params {params} -docstring {docstring} {name} {hidden}'
            s = s.format(name=r._f_name(),
                         params=params,
                         hidden=(hidden and "-hidden") or '',
                         docstring=utils.single_quoted(utils.deindent(f.__doc__ or '')))
            if enum:
                sh = '\n'.join('[ $kak_token_to_complete -eq {} ] && printf "{}\n"'.format(i, '\\n'.join(es))
                               for i, es in enumerate(enum))
                s += ' -shell-candidates %{' + sh + '} '
            s += ' %('
            s += r_pre(f)
            return s
        r.pre = pre
        r.post += ')'
        return r

    def _argnames(self):
        names = set(self.required_names)
        names.update(self.argnames)
        if self.puns:
            names.update(utils.argnames(self.f))
        return list(names)

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
        splices, self.parse = Args.argsetup(self._argnames(), self.arg_config)
        self.fifo, self.fifo_cleanup = _mkfifo()
        msg = self.pre(f) + self._msg(splices, self.fifo) + self.post
        pipe(self.session, msg, sync=self.sync_setup)
        return self.ret()

    def listen(self):
        _debug(self.f.__name__ + ' ' + self.fifo + ' waiting for call...')
        with open(self.fifo, 'r') as fp:
            line = utils.decode(fp.readline()).rstrip()
            if line == '_q':
                self.fifo_cleanup()
                _debug(self.fifo, 'demands quit')
                raise RuntimeError('fifo demands quit')
            _debug(self.f.__name__ + ' ' + self.fifo + ' replied:' + repr(line))

        r = self.parse(line)

        try:
            def _pipe(msg, sync=False):
                return pipe(self.session, msg, r['client'], sync)
            r['pipe'] = _pipe
            d = {}
            if 'reply_fifo' in r:
                d['reply_calls'] = 0

                def reply(msg):
                    d['reply_calls'] += 1
                    with open(r['reply_fifo'], 'w') as fp:
                        fp.write(msg)
                r['reply'] = reply
            result = utils.safe_kwcall(self.f, r) if self.puns else self.f(r)
            if 'reply_fifo' in r:
                if d['reply_calls'] != 1:
                    print('!!! [ERROR] Must make exactly 1 call to reply, ' +
                          self.f + ' ' + self.r + ' made ' + d['reply_calls'],
                          file=sys.stderr)
            return result
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
        import tempfile
        name = tempfile.mktemp()
        with open(name, 'wb') as tmp:
            tmp.write(utils.encode(msg))
        msg = u'eval -client {} "%sh`cat {}; rm {}`"'.format(client, name, name)
    if sync:
        fifo, fifo_cleanup = _mkfifo()
        msg += u'\n%sh(echo done > {})'.format(fifo)
    # _debug('piping: ', msg.replace('\n', ' ')[:70])
    _debug('piping: ', msg)
    if hasattr(session, '__call__'):
        session(msg)
    else:
        p = Popen(['kak', '-p', str(session).rstrip()], stdin=PIPE)
        p.stdin.write(utils.encode(msg))
        p.stdin.flush()
        p.stdin.close()
    if sync:
        _debug(fifo + ' waiting for completion...',
               msg.replace('\n', ' ')[:60])
        with open(fifo, 'r') as fifo_fp:
            fifo_fp.readline()
        _debug(fifo + ' going to clean up...')
        fifo_cleanup()
        _debug(fifo + ' done')


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

def change(range, new_text):
    """
    A command to change some text

    >>> print(change(((1,2), (3,4)), 'test'))
    select 1.2,3.4; execute-keys -draft ctest<esc> 
    """
    return select([range]) + '; execute-keys -draft c' + new_text + '<esc>'

def menu(options, auto_single=True):
    """
    A command to make a menu.

    Takes a list of 2-tuples of an entry and the command it executes.

    >>> print(menu([('one', 'echo one'), ('two', 'echo two')]))
    menu 'one' 'echo one' 'two' 'echo two'
    >>> print(menu([('one', 'echo one')]))
    echo one
    >>> print(menu([('one', 'echo one')], auto_single=False))
    menu 'one' 'echo one'
    """
    options = list(options)
    if len(options) == 1 and auto_single:
        return options[0][1]
    opts = utils.join(map(utils.single_quoted, it.chain(*options)))
    return 'menu ' + opts


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


class Args(object):

    @staticmethod
    def coord(s):
        """
        Parse a Kakoune coordinate.
        """
        return tuple(map(int, s.split('.')))

    @staticmethod
    def selection_desc(x):
        """
        Parse a Kakoune selection description.
        """
        return tuple(map(Args.coord, x.split(',')))

    @staticmethod
    def string(x):
        """
        Parse a Kakoune string.
        """
        return x

    @staticmethod
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
        ...     xs2 = Args.listof(Args.string)(exs)
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
            ms = [m.group(0)
                  for m in re.finditer(r'(.*?(?<!\\)(\\\\)*:|.+)', s)]
            ms = [m if i == len(ms) - 1 else rmlastcolon(m)
                  for i, m in enumerate(ms)]
            return [p(re.sub(r'\\(.)', '\g<1>', x)) for x in ms]
        return inner

    @staticmethod
    def boolean(s):
        """
        Parse a Kakoune boolean.
        """
        return s == 'true'

    @staticmethod
    def args_parse(s):
        return tuple(x.replace('_u', '_') for x in s.split('_S')[1:])

    @staticmethod
    def argsetup(argnames, config):
        """
        >>> s, _ = Args.argsetup('client cmd reply'.split(), {'cmd': ('a', int)})
        >>> print(s)
        ['kak_client', 'a']
        """
        args = []
        splices = []
        for name in argnames:
            try:
                if name in config:
                    splice, parse = config[name]
                else:
                    splice, parse = _arg_config[name]
                splices.append(splice)
                args.append((name, parse))
            except KeyError:
                pass

        def parse(line):
            _debug(argnames, line)
            params = [v.replace('_n', '\n').replace('_u', '_')
                      for v in line.split('_s')]
            return {name: parse(value)
                    for (name, parse), value in zip(args, params)}
        return splices, parse


_arg_config = {
    'line':   ('kak_cursor_line',   int),
    'column': ('kak_cursor_column', int),

    'aligntab':    ('kak_opt_aligntab',    Args.boolean),
    'filetype':    ('kak_opt_filetype',    Args.string),
    'indentwidth': ('kak_opt_indentwidth', int),
    'readonly':    ('kak_opt_readonly',    Args.boolean),
    'readonly':    ('kak_opt_readonly',    Args.boolean),
    'tabstop':     ('kak_opt_tabstop',     int),
    'completers':  ('kak_opt_completers',  Args.listof(Args.string)),

    'pwd':  ('PWD',  Args.string),
    'PWD':  ('PWD',  Args.string),
    'PATH': ('PATH', Args.string),
    'HOME': ('HOME', Args.string),

    'args': ('__args', Args.args_parse),
    'arg1': ('1',      Args.string),
    'arg2': ('2',      Args.string),
    'arg3': ('3',      Args.string),
    'arg4': ('4',      Args.string),
    'arg5': ('5',      Args.string),
    'arg6': ('6',      Args.string),
    'arg7': ('7',      Args.string),
    'arg8': ('8',      Args.string),
    'arg9': ('9',      Args.string),

    'bufname':            ('kak_bufname',            Args.string),
    'buffile':            ('kak_buffile',            Args.string),
    'buflist':            ('kak_buflist',            Args.listof(Args.string)),
    'timestamp':          ('kak_timestamp',          int),
    'selection':          ('kak_selection',          Args.string),
    'selections':         ('kak_selections',         Args.listof(Args.string)),
    'runtime':            ('kak_runtime',            Args.string),
    'session':            ('kak_session',            Args.string),
    'client':             ('kak_client',             Args.string),
    'cursor_line':        ('kak_cursor_line',        int),
    'cursor_column':      ('kak_cursor_column',      int),
    'cursor_char_column': ('kak_cursor_char_column', int),
    'cursor_byte_offset': ('kak_cursor_byte_offset', int),
    'selection_desc':     ('kak_selection_desc',     Args.selection_desc),
    'selections_desc':    ('kak_selections_desc',    Args.listof(Args.selection_desc)),
    'window_width':       ('kak_window_width',       int),
    'window_height':      ('kak_window_height',      int),
}


#############################################################################
# Private utils


def _mkfifo(active_fifos={}):
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
    if '-d' in sys.argv[1:]:
        print(*xs, file=sys.stderr)


#############################################################################
# Tests


def headless(ui='dummy', stdout=None):
    """
    Start a headless Kakoune process.
    """
    p = Popen(['kak', '-n', '-ui', ui], stdout=stdout)
    time.sleep(0.01)
    return p


def _test_remote_commands_sync():
    u"""
    >>> kak = headless()
    >>> @Remote.command(kak.pid, sync_setup=True)
    ... def write_position(line, column, pipe):
    ...      pipe(utils.join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''), sync=True)
    >>> pipe(kak.pid, 'write-position', 'unnamed0', sync=True)
    >>> pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> write_position('unnamed0')
    >>> pipe(kak.pid, 'exec \%H', 'unnamed0', sync=True)
    >>> print(Remote.onclient(kak.pid, 'unnamed0')(
    ...     lambda selection: selection))
    1:1, 1:5
    >>> r = Remote(kak.pid)
    >>> r.puns = False
    >>> r.required_names.add('selection')
    >>> print(r.onclient(r, 'unnamed0', sync=True)(lambda d: d['selection']))
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


def _test_unicode_and_escaping():
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


def _test_remote_commands_async():
    u"""
    >>> kak = headless()
    >>> @Remote.command(kak.pid)
    ... def write_position(pipe, line, column):
    ...      pipe(utils.join(('exec ', 'a', str(line), ':', str(column), '<esc>'), sep=''))
    >>> pipe(kak.pid, 'write-position', 'unnamed0')
    >>> time.sleep(0.05)
    >>> pipe(kak.pid, 'exec a,<space><esc>', 'unnamed0', sync=True)
    >>> time.sleep(0.02)
    >>> write_position('unnamed0')
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


def _test_commands_with_params():
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
