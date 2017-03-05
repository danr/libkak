# -*- coding: utf-8 -*-
from __future__ import print_function
import inspect
import sys
import os
import tempfile
from functools import wraps
from subprocess import Popen, PIPE
from contextlib import contextmanager
from collections import namedtuple
from threading import Thread
import threading
import time
import shutil
import six

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


def debug(*ws):
    return
    print(threading.current_thread().name, *ws)


def headless():
    proc = Popen(['kak','-n','-ui','dummy'])
    time.sleep(0.01)
    kak = Kak('pipe', proc.pid, 'unnamed0')
    kak.sync()
    return kak


def unconnected():
    return Kak('unconnected')


@contextmanager
def identity_manager():
    yield


def modify_manager(manager=None, pre=None, post=None):
    if manager is None:
        manager = identity_manager
    @contextmanager
    def k():
        if pre:
            pre()
        with manager() as a:
            yield a
        if post:
            post()
    return k


@contextmanager
def nest(*managers):
    """
    Runs the managers in order, skipping the None ones.

    >>> def manager(msg):
    ...     @contextmanager
    ...     def k():
    ...         print(msg + ' begin')
    ...         yield msg
    ...         print(msg + ' end')
    ...     return k
    >>> with nest(manager('a'), manager('b'), None, manager('c')) as abc:
    ...     print(abc)
    a begin
    b begin
    c begin
    ('a', 'b', None, 'c')
    c end
    b end
    a end
    """
    if managers:
        with (managers[0] or identity_manager)() as m:
            with nest(*managers[1:]) as ms:
                yield (m,) + ms
    else:
        yield ()


def single_quote_escape(string):
    """
    Backslash-escape ' and \.
    """
    return string.replace(u'\\', u'\\\\').replace(u"'", u"\\'")


def single_quoted(string):
    u"""
    The string wrapped in single quotes and escaped.

    >>> print(single_quoted(u"i'ié"))
    'i\\'ié'
    """
    return u"'" + single_quote_escape(string) + u"'"


class Flag(object):
    def __init__(self, flag, value=True):
        self.flag = flag
        self.value = value

    def show(self):
        """
        Show

        >>> Flag('no-hooks').show()
        '-no-hooks'
        >>> print(Flag('try-client', 'unnamed1').show())
        -try-client 'unnamed1'
        """
        if self.value is True:
            return '-' + self.flag
        else:
            return '-' + self.flag + ' ' + single_quoted(self.value)

    def __repr__(self):
        if self.value is True:
            return 'Flag(' + repr(self.flag) + ')'
        else:
            return 'Flag' + repr((self.flag, self.value))


def show_flags(flags):
    """
    >>> print(show_flags([Flag('no_hooks'), Flag('try_client', 'unnamed1')]))
    -no_hooks -try_client 'unnamed1'
    """
    return join(flag.show() for flag in flags)


def filter_flags(xs):
    """
    Separates flags and other objects.

    >>> filter_flags([Flag('draft'), Flag('no_hooks'), '%', 'y'])
    ([Flag('draft'), Flag('no_hooks')], ['%', 'y'])
    """
    flags = []
    other = []
    for x in xs:
        if isinstance(x, Flag):
            flags.append(x)
        else:
            other.append(x)
    return flags, other


class Query(namedtuple('Query', ['kak', 'variable', 'parse'])):
    """
    Call the query to ask its value, or aggregate several using ask.
    """
    def __call__(self):
        return self.kak._ask((self,))[0]

    def variable_for_sh(self):
        if isinstance(self.variable, int):
            return str(self.variable)
        else:
            return "kak_" + self.variable

    @staticmethod
    def parse_intlist(s):
        return [ int(x) for x in s.split(':') ]

    @staticmethod
    def parse_strlist(s):
        return [ x.replace('\:', ':') for x in s.split(':') ]


"""
>>> for n in range(100):
...     s = ''.join(random.choice([chr(10), chr(92), ':', '_', 's', 'u'])
...                                for _ in range(n))
...     kak = libkak.headless()
...     # set buffer to s
...     # pick random positions in s, and set selections to that
...     # check that querying selections give the right results from s
"""


class val(object):
    def __init__(self, kak):
        def coord(s):
            return tuple(map(int, s.split('.')))

        def selection_desc(x):
            return tuple(map(coord, x.split(',')))

        def string(x):
            return x

        def listof(p):
            return lambda xs: [p(x) for x in xs.split(':')]

        self.bufname=Query(kak, 'bufname', string)
        self.buffile=Query(kak, 'buffile', string)
        self.buflist=Query(kak, 'buflist', listof(string))
        self.timestamp=Query(kak, 'timestamp', int)
        self.selection=Query(kak, 'selection', string)
        self.selections=Query(kak, 'selections', listof(string))
        self.runtime=Query(kak, 'runtime', string)
        self.session=Query(kak, 'session', string)
        self.client=Query(kak, 'client', string)
        self.cursor_line=Query(kak, 'cursor_line', int)
        self.cursor_column=Query(kak, 'cursor_column', int)
        self.cursor_char_column=Query(kak, 'cursor_char_column', int)
        self.cursor_byte_offset=Query(kak, 'cursor_byte_offset', int)
        self.selection_desc=Query(kak, 'selection_desc', selection_desc)
        self.selections_desc=Query(kak, 'selections_desc', listof(selection_desc))
        self.window_width=Query(kak, 'window_width', int)
        self.window_height=Query(kak, 'window_height', int)

class dynamic(object):
    """
    Access to dynamic variables: options, registers and env vars.

    >>> kak = libkak.headless()
    >>> kak.send('declare-option str executable python2')
    >>> print(kak.opt.executable())
    python2
    >>> kak.opt.executable = 'python3'
    >>> print(*kak.ask(kak.opt.executable))
    python3
    >>> kak.quit()

    >>> kak = libkak.headless()
    >>> kak.execute("itest<esc>Gh")
    >>> print(*kak.ask(kak.reg['.'], kak.reg.hash))
    test 1
    >>> kak.quit()
    """

    def __init__(self, kak, prefix, assign_cmd):
        self._kak = kak
        self._prefix = prefix
        self._assign_cmd = assign_cmd
        self._ready = True

    def __getattr__(self, name):
        return Query(self._kak, self._prefix + '_' + name, lambda x: x)

    def __getitem__(self, name):
        reg_names = {
            '_': "underscore",
            '/': "slash",
            '"': "dquote",
            '|': "pipe",
            '^': "caret",
            '@': "arobase",
            '%': "percent",
            '.': "dot",
            '#': "hash"
        }
        return self.__getattr__(reg_names.get(name, name))

    def __setattr__(self, name, value):
        if '_ready' not in self.__dict__:
            self.__dict__[name] = value
        elif self._assign_cmd:
            self._kak.send(self._assign_cmd, name, value)
        else:
            raise RuntimeError('Cannot assign to ' + self._prefix)


class Kak(object):

    def execute(kak, *keys_and_flags):
        """
        buffer=None, client=None, try_client=None, draft=False, no_hooks=False,
        itersel=False, save_regs=None, collapse_jumps=False
        """
        flags, keys = filter_flags(keys_and_flags)
        kak.send("exec", show_flags(flags), single_quoted(join(keys, sep='')))


    def evaluate(kak, *cmds_and_flags):
        """
        eval
        """
        flags, cmds = filter_flags(cmds_and_flags)
        kak.send("eval", show_flags(flags), "'")
        with kak.end_quote():
            for cmd in cmds:
                kak.send(cmd)


    def info(kak, *text_and_flags):
        """
        anchor=None, placement=None, title=None
        """
        flags, text = filter_flags(text_and_flags)
        kak.send("info", show_flags(flags), single_quoted(join(text)))


    def echo(kak, *text_and_flags):
        """
        color=None, markup=False, debug=False
        """
        flags, text = filter_flags(text_and_flags)
        kak.send("echo", show_flags(flags), single_quoted(join(text)))


    def set_option(kak, scope, option, value, add=False):
        raise NotImplemented


    def set_register(kak, reg, value):
        raise NotImplemented


    def quit(kak, force=True):
        """
        Quit the current client.

        Runs quit! when force is True.
        """
        kak.evaluate('quit' + ('!' if force else ''))
        kak._flush()
        while kak._main._ears:
            ear, _ = kak._main._ears.popitem()
            try:
                with open(ear, 'w') as f:
                    f.write('_q')
            except IOError:
                pass
        try:
            shutil.rmtree(kak._dir)
        except FileNotFoundError:
            shutil.rmtree(kak._dir)


    # edit
    # map
    # write
    # call/evaluate
    # try..catch..
    # highlighters?

    def __init__(kak, channel='stdout', session=None, client=None):
        """
        Initialize a Kak object.

        Set channel to 'pipe' and a session pid/name (kak -p).

        For testing use the functions headless and unconnected.
        """
        kak._ears     = {}
        kak._messages = []
        kak._session  = session
        kak._client   = client
        kak._main     = kak
        kak._counter  = 0
        kak._send     = lambda words: kak._messages.append(join(words))
        kak.val       = val(kak)
        kak.opt       = dynamic(kak, 'opt', 'set-option current')
        kak.env       = dynamic(kak, 'client_env', '')
        kak.reg       = dynamic(kak, 'reg', 'set-register')
        if channel is 'unconnected':
            kak._channel = None
        else:
            kak._channel = channel # 'stdout', 'pipe', None or a fifo filename
            kak._dir = tempfile.mkdtemp()



    def send(kak, *words):
        """
        Send a raw message to kak.

        This is buffered and all messages are transmitted in one go.
        """
        debug('Sending: ', words)
        kak._send(words)


    def join(kak):
        """
        Daemonize and wait for all threads to finish.
        """
        raise NotImplemented


    def release(kak):
        """
        Release control over kak and continue asynchronously.
        """
        if not kak._session:
            debug('getting session&client')
            kak._session, kak._client = kak.ask(kak.val.session, kak.val.client)
        debug('releasing')
        kak._flush()
        debug('channel is now pipe')
        kak._channel = 'pipe'


    def debug_sent(kak):
        return join(kak._messages, u'\n')


    def _flush(kak):
        """
        Flush everything to be sent.

        This is probably not the function you are looking for.
        Perhaps you want ``release``, ``ask`` or ``join``?
        """

        chunk = join(kak._messages, sep=u'\n') + u'\n'

        if kak._client and kak._channel == 'pipe':
            chunk = u'eval -client ' + kak._client + u" '\n" + single_quote_escape(chunk) + u"\n'"

        assert isinstance(chunk, six.string_types)
        # print(chunk)

        if not kak._channel:
            raise ValueError('Need a channel to kak')
        if kak._channel == 'stdout':
            debug('stdout chunk', chunk)
            print(chunk)
        elif kak._channel == 'pipe':
            if not kak._session:
                raise ValueError('Cannot pipe to kak without session details')
            p = Popen(['kak','-p',str(kak._session)], stdin=PIPE)
            debug('piping chunk', chunk)
            p.communicate(encode(chunk))
            p.wait()
            debug('waiting finished')
        else:
            debug('writing to ', kak._channel)
            debug('sending chunk:', chunk)
            with open(kak._channel, 'wb') as f:
                f.write(encode(chunk))
            debug('writing done ', kak._channel)

        kak._messages=[]
        kak._channel=None


    def _mkfifo(kak):
        kak._counter += 1
        name = kak._dir + '/' + str(kak._counter)
        os.mkfifo(name)
        return name


    def _duplicate(kak):
        new_kak = Kak(channel=None, session=kak._session, client=kak._client)
        new_kak._main   = kak._main
        return new_kak


    def _fork(kak):
        def decorate(target):
            new_kak = kak._duplicate()
            def target_and_then_cleanup():
                target(new_kak)
                shutil.rmtree(new_kak._dir)
            thread = Thread(target=target_and_then_cleanup)
            thread.start()
        return decorate


    @contextmanager
    def sh(kak):
        """
        Context manager for making %sh{..} splices.

        >>> kak = libkak.unconnected()
        >>> with kak.sh():
        ...     kak.send("echo echo '$PWD='" + '"$PWD"')
        >>> print(kak.debug_sent())
        %sh'
          echo echo \\'$PWD=\\'"$PWD"
        '
        """
        kak.send("%sh'")
        with kak.end_quote():
            yield


    @contextmanager
    def end_quote(kak):
        """
        Context manager for escaping ' and ending with a '.

        >>> kak = libkak.unconnected()
        >>> kak.send("try '")
        >>> with kak.end_quote():
        ...     kak.send("exec <a-k>\w'<ret>")
        >>> print(kak.debug_sent())
        try '
          exec <a-k>\\\\w\\'<ret>
        '
        """
        with kak._local(single_quote_escape):
            yield
        kak.send("'")


    @contextmanager
    def _local(kak, word_modifier):
        """
        Context manager for preprocessing each word to the send function.
        """
        parent = kak._send
        kak._send = lambda words: parent((' ',) + tuple(map(word_modifier, words)))
        yield
        kak._send = parent


    def _setup_query(kak, queries, extra_manager=None, reentrant=False):

        from_kak = kak._mkfifo()

        with nest(extra_manager, kak.sh):
            qvars = []

            debug('queries:', queries)
            for i, q in enumerate(queries):
                qvar = "__kak_q"+str(i)
                kak.send(qvar+'=${'+q.variable_for_sh()+'//_/_u}')
                kak.send(qvar+'=${'+qvar+'//\n/_n}')
                qvars.append('${'+qvar+'}')

            kak.send('reply_dir=$(mktemp -d)')
            kak.send('reply_fifo=$reply_dir/fifo')
            kak.send('mkfifo $reply_fifo')
            qvars.append('${reply_fifo//_/_u}')

            kak.send('echo', '-n', '"' + '_s'.join(qvars) + '_S"', '>', from_kak)

            kak.send('cat ${reply_fifo}')
            kak.send('rm ${reply_fifo}')
            kak.send('rmdir ${reply_dir}')
            if not reentrant:
                kak.send('rm', from_kak)

        def handle():
            debug('waiting for kak to reply on', from_kak)
            kak._main._ears[from_kak] = ()
            with open(from_kak, 'rb') as f:
                responses = decode(f.read())
            debug('Got response: ' + responses)
            if u'_q' in responses:
                raise RuntimeError('Quit has been called')
            del kak._main._ears[from_kak]

            all_answers = []
            for response_line in responses.split(u'_S'):
                if response_line:
                    raw = [ans.replace(u'_n', u'\n').replace(u'_u', u'_')
                           for ans in response_line.split(u'_s')]
                    to_kak = raw.pop()
                    debug(to_kak, repr(raw))
                    answers = tuple(q.parse(ans) for ans, q in zip(raw, queries))
                    all_answers.append((to_kak, answers))

            if reentrant:
                return all_answers
            else:
                return all_answers[0]

        return handle


    def ask(kak, *questions):
        """
        Ask for the answers of multiple queries.

        This is more efficient than calling the queries one-by-one.

        Blocks the python thread until the answers have arrived.

        >>> kak = libkak.headless()
        >>> kak.ask(kak.val.cursor_line, kak.val.cursor_column)
        (1, 1)
        >>> kak.quit()
        """
        return kak._ask(questions)


    def _ask(kak, questions, extra_manager=None, allow_noop=True):
        """
        Ask for the answers of some questions.

        (Unless allow_noop is True (default) and the questions list is empty.)

        When the answer comes, kak is blocked until we respond to it
        (using ``ask``, ``release``, etc).
        This means that if we are connecting to a running kak process,
        from now on it starts blocking.
        """
        if not questions and allow_noop:
            with modify_manager(extra_manager):
                return ()
        else:
            handle = kak._setup_query(questions, extra_manager=extra_manager)
            kak._flush()
            to_kak, answers = handle()
            debug('yay:', to_kak, answers)
            kak._channel = to_kak
            return answers


    def _reentrant_query(kak, f, questions, extra_manager=None):
        """
        Make a query, but listen for answers forever and respond by calling f.

        Used for responding indefinitely to define-command calls and hooks.
        """
        handle = kak._setup_query(questions,
                                  extra_manager=extra_manager,
                                  reentrant=True)
        @kak._fork()
        def dispatcher(ctx):
            while True:
                try:
                    debug('dispatching listen')
                    all_answers = handle()
                    debug('dispatching received', repr(all_answers))
                except RuntimeError:
                    return
                if len(all_answers) > 1:
                    debug('with backlog:', len(all_answers))
                for to_kak, answers in all_answers:
                    debug('handling one', to_kak)
                    ctx._channel = to_kak
                    f(ctx, *answers)
                    ctx._flush()


    def sync(kak):
        kak._ask([], allow_noop=False)


    def hook(kak, scope, hook_name, filter='.*', group=None):
        """
        Make a kakoune hook.

        Intended to be used as a decorator.

        The decorated function can be called and will execute the same as the
        hook does when triggered.

        >>> kak = libkak.headless()
        >>> @kak.hook('global', 'InsertChar')
        ... def insert_ascii(ctx, char):
        ...     hex = format(ord(char), '02X')
        ...     ctx.execute(Flag('no-hooks', True), ':', hex, '|')
        >>> kak.execute('iRace<space>condition<esc>Gh')
        >>> print(kak.val.selection())
        R:52|a:61|c:63|e:65| :20|c:63|o:6F|n:6E|d:64|i:69|t:74|i:69|o:6F|n:6E|
        >>> kak.execute('di')
        >>> insert_ascii(kak, 'A')
        >>> kak.execute('<esc>Gh')
        >>> print(kak.val.selection())
        :41|
        >>> kak.quit()
        """
        def decorate(f):
            queries = [Query(kak, 'hook_param', str)]

            flag = '-group ' + group if group else ''
            kak.send('hook', flag, scope, hook_name, repr(filter), "'")
            kak._reentrant_query(f, queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, hook_param):
                return f(ctx, hook_param)
            return call_from_python
        return decorate


    def cmd(kak, hidden=True, allow_override=True):
        """
        Make a kakoune command (`def`/`define-command`).

        Intended to be used as a decorator.
        You can use parameters with queries as default values,
        and the function will be called with the answers for these.
        The first argument is the context to communicate with kak.

        Not implemented: polyvariadic params, completion
        The *varargs parameter should get all (remaining?) arguments.

        >>> kak = libkak.headless()
        >>> @kak.cmd()
        ... def write_position(ctx, y=kak.val.cursor_line,
        ...                         x=kak.val.cursor_column):
        ...      ctx.execute('a', str(y), ':', str(x), '<esc>')
        >>> kak.evaluate('write_position')
        >>> kak.execute('a,<space><esc>')
        >>> write_position(kak)
        >>> kak.execute('xH')
        >>> print(kak.val.selection())
        1:1, 1:5
        >>> kak.quit()
        """
        def decorate(f):
            spec = inspect.getargspec(f)
            n_as = len(spec.args) - 1
            defaults = spec.defaults or []
            n_qs = len(defaults)
            if n_as < n_qs:
                raise ValueError('Cannot have a default value for the new context.')

            queries = [Query(kak, 1+i, str) for i in range(0, n_as - n_qs)]
            queries.extend(defaults)

            flags=['-params ' + str(n_as - n_qs)]
            if hidden:
                flags.append('-hidden')
            if allow_override:
                flags.append('-allow-override')
            if f.__doc__:
                flags.append('-docstring ' + repr(f.__doc__))

            kak.send('def', join(flags), f.__name__, "'")
            kak._reentrant_query(f, queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, *args):
                debug('calling', f.__name__, 'default args:', len(defaults))
                if len(args) != n_as - n_qs:
                    raise ValueError('Wrong number of arguments')
                return f(ctx, *(args + ctx._ask(defaults, allow_noop=True)))
            return call_from_python
        return decorate


    def on_key(kak, questions=[], before_blocking=None):
        """
        Run on-key and optionally asks questions, get the key and the answers.

        Set the before_blocking callback if you want to do something before
        listening for the key (example: test this function).

        >>> kak = libkak.headless()
        >>> kak.on_key([kak.val.cursor_line],
        ...            before_blocking=lambda: kak.execute('z'))
        ('z', 1)
        >>> kak.quit()
        """
        kak.send('on-key', "'")
        manager = modify_manager(kak.end_quote, post=before_blocking)
        return kak._ask([Query(kak, "key", str)] + questions, extra_manager=manager)


    def prompt(kak, message='', questions=[], init=None, before_blocking=None):
        """
        Run prompt and optionally asks questions, get the text and the answers.

        Not implemented: -init

        Set the before_blocking callback if you want to do something before
        listening for the key (example: test this function).

        >>> kak = libkak.headless()
        >>> kak.prompt(questions=[kak.val.cursor_line],
        ...            before_blocking=lambda: kak.execute('user_input<ret>'))
        ('user_input', 1)
        >>> kak.quit()
        """
        flag = '-init ' + init if init else ''
        kak.send('prompt', flag, repr(message), "'")
        manager = modify_manager(kak.end_quote, post=before_blocking)
        return kak._ask([Query(kak, "text", str)] + questions, extra_manager=manager)


def _query_test():
    """
    >>> kak = libkak.headless()
    >>> kak.execute('100o<c-r>#<esc>%')
    >>> kak.release()
    >>> kak.ask(kak.val.cursor_line, kak.val.cursor_column, kak.val.selection) \
            == (kak.val.cursor_line(), kak.val.cursor_column(), kak.val.selection())
    True
    >>> kak.quit()
    """
    pass


def _cmd_test():
    """
    >>> kak = headless()
    >>> @kak.cmd()
    ... def test(ctx, txt, y=kak.val.cursor_line):
    ...     ctx.execute("oTest!<space>", txt, "<space>", str(y), "<esc>")
    ...     return ctx.val.selection()
    >>> debug('asking...')
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



if __name__ == '__main__':
    import doctest
    import sys
    doctest.testmod(extraglobs={'libkak': sys.modules[__name__]})
    sys.exit()
    dt_runner = doctest.DebugRunner()
    tests = doctest.DocTestFinder().find(sys.modules[__name__])
    for t in tests:
        t.globs['libkak']=sys.modules[__name__]
        #dt_runner.run(t)
        try:
            dt_runner.run(t)
        except doctest.UnexpectedException as e:
            import pdb
            pdb.post_mortem(e.exc_info[2])

