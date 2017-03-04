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


def debug(*ws):
    return
    print(threading.current_thread().name, *ws)


def headless():
    proc = Popen(['kak','-n','-ui','dummy'])
    kak = Kak('pipe', proc.pid, 'unnamed0')
    kak._ask([], allow_noop=False)
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
    return string.replace('\\', '\\\\').replace("'", "\\'")


def single_quoted(string):
    """
    The string wrapped in single quotes and escaped.

    >>> single_quoted("i'i")
    "'i\\\\'i'"
    """
    return "'" + single_quote_escape(string) + "'"


class Flag(object):
    def __init__(self, flag, value=True):
        self.flag = flag
        self.value = value

    def show(self):
        """
        Show

        >>> Flag('no-hooks').show()
        '-no-hooks'
        >>> Flag('try-client', 'unnamed1').show()
        "-try-client 'unnamed1'"
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
    >>> show_flags([Flag('no_hooks'), Flag('try_client', 'unnamed1')])
    "-no_hooks -try_client 'unnamed1'"
    """
    return ' '.join(flag.show() for flag in flags)


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


def val_queries(kak):
    class val(object):
        cursor_line=Query(kak, 'kak_cursor_line', int)
        cursor_column=Query(kak, 'kak_cursor_column', int)
        selection=Query(kak, 'kak_selection', str)
        selections=Query(kak, 'kak_selections', lambda s: s.split(':'))
        session=Query(kak, 'kak_session', int)
        client=Query(kak, 'kak_client', str)
    return val()


class Kak(object):

    def execute(kak, *keys_and_flags):
        """
        buffer=None, client=None, try_client=None, draft=False, no_hooks=False,
        itersel=False, save_regs=None, collapse_jumps=False
        """
        flags, keys = filter_flags(keys_and_flags)
        kak.send("exec", show_flags(flags), single_quoted(''.join(keys)))


    execute.no_hooks = Flag('no-hooks')


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
        kak.send("info", show_flags(flags), single_quoted(' '.join(text)))


    def echo(kak, *text_and_flags):
        """
        color=None, markup=False, debug=False
        """
        flags, text = filter_flags(text_and_flags)
        kak.send("echo", show_flags(flags), single_quoted(' '.join(text)))


    def set_option(kak, scope, option, value, add=False):
        raise NotImplemented


    def set_register(kak, reg, value):
        raise NotImplemented


    def quit(kak, force=False):
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
        kak._send     = lambda words: kak._messages.append(' '.join(words))
        kak.val       = val_queries(kak)
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
        return '\n'.join(kak._messages)


    def _flush(kak):
        """
        Flush everything to be sent.

        This is probably not the function you are looking for.
        Perhaps you want ``release``, ``ask`` or ``join``?
        """

        chunk = '\n'.join(kak._messages) + '\n'

        if kak._client and kak._channel == 'pipe':
            chunk = 'eval -client ' + kak._client + " '\n" + single_quote_escape(chunk) + "\n'"

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
            p.communicate(chunk)
            p.wait()
            debug('waiting finished')
        else:
            debug('writing to ', kak._channel)
            debug('sending chunk:', chunk)
            with open(kak._channel, 'w') as f:
                f.write(chunk)
            debug('writing done ', kak._channel)

        kak._messages=[]
        kak._channel=None


    def to_client(kak, client):
        kak.client = client


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
        to_kak = kak._mkfifo()

        single_query = len(queries) == 1

        with nest(extra_manager, kak.sh):
            qvars = []

            debug('queries:', queries)
            if single_query:
                qvars.append('${'+queries[0].variable+'}')
            else:
                for i, q in enumerate(queries):
                    qvar = "__kak_q"+str(i)
                    kak.send(qvar+'=${'+q.variable+'//_/_u}')
                    qvars.append('${'+qvar+'}')
            kak.send('echo', '-n', '"' + '_s'.join(qvars) + '"', '>', from_kak)
            kak.send('cat', to_kak)
            kak.send('rm', to_kak)
            if not reentrant:
                kak.send('rm', from_kak)

        def handle():
            debug('waiting for kak to reply on', from_kak)
            kak._main._ears[from_kak] = ()
            with open(from_kak, 'r') as f:
                response = f.read()
            debug('Got response: ' + response)
            if response == '_q':
                raise RuntimeError('Quit has been called')
            del kak._main._ears[from_kak]
            if single_query:
                answers = (queries[0].parse(response), )
            else:
                answers = tuple(q.parse(ans.replace('_u', '_'))
                                for ans, q in zip(response.split('_s'), queries))

            return to_kak, answers

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
        def dispatcher(dispatch_ctx):
            while True:
                try:
                    debug('dispatching listen')
                    to_kak, answers = handle()
                    debug('dispatching received', to_kak, answers)
                except RuntimeError:
                    return
                @kak._fork()
                def handle_one(ctx):
                    debug('handling one', to_kak)
                    ctx._channel = to_kak
                    f(ctx, *answers)
                    ctx._flush()


    def hook(kak, scope, hook_name, filter='.*', group=None):
        """
        Make a kakoune hook.

        Intended to be used as a decorator.

        The decorated function can be called and will execute the same as the
        hook does when triggered.

        >>> kak = libkak.headless()
        >>> @kak.hook('global', 'InsertChar')
        ... def insert_ascii(ctx, char):
        ...     ctx.execute(Flag('no-hooks', True), ':', str(ord(char)), '<space>')
        >>> kak.execute('iKak<esc>%')
        >>> kak.val.selection()
        'K:75 a:97 k:107 \\n'
        >>> kak.execute('di')
        >>> insert_ascii(kak, 'A')
        >>> kak.execute('<esc>%')
        >>> kak.val.selection()
        ':65 \\n'
        >>> kak.quit()
        """
        def decorate(f):
            queries = [Query(kak, 'kak_hook_param', str)]

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
        >>> kak.execute('%')
        >>> kak.val.selection()
        '1:1, 1:5\\n'
        >>> kak.quit()
        """
        def decorate(f):
            spec = inspect.getargspec(f)
            n_as = len(spec.args) - 1
            defaults = spec.defaults or []
            n_qs = len(defaults)
            if n_as < n_qs:
                raise ValueError('Cannot have a default value for the new context.')

            queries = [Query(kak, str(1+i), str) for i in range(0, n_as - n_qs)]
            queries.extend(defaults)

            flags=['-params ' + str(n_as - n_qs)]
            if hidden:
                flags.append('-hidden')
            if allow_override:
                flags.append('-allow-override')
            if f.__doc__:
                flags.append('-docstring ' + repr(f.__doc__))

            kak.send('def', ' '.join(flags), f.__name__, "'")
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
        return kak._ask([Query(kak, "kak_key", str)] + questions, extra_manager=manager)


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
        return kak._ask([Query(kak, "kak_text", str)] + questions, extra_manager=manager)


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

