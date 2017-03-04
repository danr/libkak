from __future__ import print_function
import inspect
import sys
import os
import tempfile
from functools import wraps
from subprocess import Popen, PIPE
from contextlib import contextmanager
from collections import namedtuple


def headless():
    proc = Popen(['kak','-n','-ui','dummy'])
    kak = Kak('pipe', proc.pid, 'unnamed0')
    kak._ask([], allow_noop=False)
    return kak


def unconnected():
    return Kak(None)


@contextmanager
def identity_manager():
    yield


@contextmanager
def modify_manager(manager=None, pre=None, post=None):
    if manager is None:
        manager = identity_manager
    if pre:
        pre()
    with manager() as a:
        yield a
    if post:
        post()


@contextmanager
def nest(*managers):
    """
    Runs the managers in order, skipping the None ones.

    >>> @contextmanager
    ... def manager(msg):
    ...     print(msg + ' begin')
    ...     yield msg
    ...     print(msg + ' end')
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
        with managers[0] or identity_manager() as m:
            with nest(*managers[1:]) as ms:
                yield (m,) + ms
    else:
        yield ()


def single_quote_escape(string):
    """
    Backslash-escape ' and \.

    >>> single_quote_escape("'\\'")
    "\\\\'\\\\'"
    """
    return string.replace('\\', '\\\\').replace("'", "\\'")


def single_quoted(string):
    """
    The string wrapped in single quotes and escaped.

    >>> single_quoted("i'i")
    "'i\\\\'i'"
    """
    return "'" + single_quote_escape(string) + "'"


def replace(string, *replacement_tuples):
    """
    Perform many str.replace replacements.

    >>> replace('_n_ub', ('_n', 'NL'), ('_u', '_'))
    'NL_b'
    """
    for s, t in replacement_tuples:
        string = string.replace(s, t)
    return string


class Flag(object):
    def __init__(self, flag, value=True):
        self.flag = flag
        self.value = value

    def show(self):
        """
        >>> Flag('no_hooks').show()
        '-no_hooks'
        >>> Flag('try_client', 'unnamed1').show()
        "-try_client 'unnamed1'"
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


Query = namedtuple('Query', ['variable', 'parse'])


class Kak(object):
    class val(object):
        cursor_line=Query('kak_cursor_line', int)
        cursor_column=Query('kak_cursor_column', int)
        session=Query('kak_session', int)
        client=Query('kak_client', str)

    def execute(kak, *keys_and_flags):
        """
        buffer=None, client=None, try_client=None, draft=False, no_hooks=False,
        itersel=False, save_regs=None, collapse_jumps=False
        """
        flags, keys = filter_flags(keys_and_flags)
        kak.send("exec", show_flags(flags), single_quoted(''.join(keys)))


    execute.no_hooks = Flag('no_hooks')


    def evaluate(kak, *cmds_and_flags):
        """
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
        kak._messages = []
        kak._channel  = channel # 'stdout', 'pipe', None or a fifo filename
        kak._session  = session
        kak._client   = client
        kak._main     = kak
        # def snd(words):
            # import pdb; pdb.set_trace()
            # kak._messages.append(' '.join(words))
        kak._send     = lambda words: kak._messages.append(' '.join(words))


    def send(kak, *words):
        """
        Send a raw message to kak.

        This is buffered and all messages are transmitted in one go.
        """
        # print('Sending: ', words)
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
            kak._session, kak._client = kak.ask(kak.val.session, kak.val.client)
        kak._flush()
        kak._channel = 'pipe'


    def debug_sent(kak):
        return '\n'.join(kak._messages)


    def _flush(kak, soft=False):
        """
        Flush everything that has been sent.

        This is probably not the function you are looking for.
        Perhaps you want ``release``, ``ask`` or ``join``?

        Soft means writing what we have so far if communicating via stdout
        and a noop when writing anywhere else.
        """
        if soft and kak._channel != 'stdout':
            return

        chunk = '\n'.join(kak._messages) + '\n'

        if kak._client and kak._channel == 'pipe':
            chunk = 'eval -client ' + kak._client + " '\n" + single_quote_escape(chunk) + "\n'"

        if not kak._channel:
            raise ValueError('Need a channel to kak')
        if kak._channel == 'stdout':
            print(chunk)
        elif kak._channel == 'pipe':
            if not kak._session:
                raise ValueError('Cannot pipe to kak without session details')
            p = Popen(['kak','-p',str(kak._session)], stdin=PIPE)
            # print(chunk)
            p.communicate(chunk)
            p.wait()
            # print('waiting finished')
        else:
            # print('writing to ', kak._channel)
            with open(kak._channel, 'w') as f:
                f.write(chunk)
            # print('writing done ', kak._channel)

        kak._messages=[]
        if not soft:
            kak._channel=None


    def to_client(kak, client):
        kak.client = client


    def _mkfifo(kak):
        dir = tempfile.mkdtemp()
        name = dir + '/fifo'
        os.mkfifo(name)
        return name

    def _duplicate(kak):
        new_kak = Kak(None, kak._session)
        new_kak._client = kak._client
        new_kak._main   = kak._main
        return new_kak


    def _register_thread(kak):
        raise NotImplemented


    def _fork(kak):
        def decorate(target):
            new_kak = kak._duplicate()
            thread = Thread(target=target, args=(new_kak,))
            thread.start()
            thread.run()
            kak._register_thread(thread)
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
          exec <a-k>\\w\'<ret>
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


    def _setup_query(kak, queries, extra_manager=None, single_query=False):
        from_kak = kak._mkfifo()
        to_kak = kak._mkfifo()

        with nest(extra_manager, kak.sh()):
            qvars = []
            # print(queries)
            if single_query:
                assert len(queries) == 1
                qvars.append('${'+queries[0].variable+'}')
            else:
                for i, q in enumerate(queries):
                    qvar = "__kak_q"+str(i)
                    kak.send(qvar+'=${'+q.variable+'//_/_u}')
                    kak.send(qvar+'=${'+qvar+'//\\n/_n}')
                    qvars.append('${'+qvar+'}')
            kak.send('echo', '-n', '_s'.join(qvars), '>', from_kak)
            kak.send('cat', to_kak)
            kak.send('rm', from_kak, to_kak)

        def handle():
            with open(from_kak, 'r') as f:
                response_line = f.readline()
                # why readine?... we use the fifo only once anyway
                # todo: test if the fifo can be used several times...
            if single_query:
                answers = (queries[0].parse(response_line), )
            else:
                answers = tuple(q.parse(replace(ans, ('_n', '\n'), ('_', '')))
                                for ans, q in zip(response_line.split('_s'), queries))

            return from_kak, to_kak, answers

        return handle


    def ask(kak, *questions):
        """
        Ask for the answers of some questions.

        Blocks the python thread until the answers have arrived.

        >>> kak = libkak.headless()
        >>> kak.ask(kak.val.cursor_line)
        1
        >>> kak.quit()
        """
        return kak._ask(questions)


    def _ask(kak, questions, extra_manager=None, allow_noop=True,
                  single_query=False):
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
            handle = kak._setup_query(questions, extra_manager=extra_manager,
                                                 single_query=single_query)
            kak._flush()
            from_kak, to_kak, answers = handle()
            # print('yay:', from_kak, to_kak, answers)
            kak._channel = to_kak
            return answers


    def _reentrant_query(kak, f, questions, extra_manager=None):
        """
        Make a query, but listen for answers forever and respond by calling f.

        Used for responding indefinitely to define-command calls and hooks.
        """
        handle = kak._setup_query(questions, extra_manager=extra_manager)
        kak._flush(soft=True)
        @kak._fork
        def dispatcher(_kak):
            while True:
                _from_kak, to_kak, answers = handle()
                @kak._fork
                def handle_one(ctx):
                    ctx._channel = to_kak
                    f(ctx, *answers)


    def hook(kak, scope, hook_name, filter='.*', extra_queries=[], group=None):
        """
        Make a kakoune hook.

        Intended to be used as a decorator.

        The decorated function can be called and will execute the same as the
        hook does when triggered.

        >>> kak = libkak.headless()
        >>> @kak.hook('global', 'InsertChar')
        ... def insert_ascii(ctx, char):
        ...     ctx.execute(':', str(ord(char)), '<space>')
        >>> kak.execute('iKak<esc>%')
        >>> kak.ask(kak.val.selection)
        'K:75 a:97 k:107 \\n'
        >>> kak.execute('di')
        >>> insert_ascii(kak, 'A')
        >>> kak.execute('<esc>%')
        >>> kak.ask(kak.val.selection)
        'A:65 \\n'
        >>> kak.quit()
        """
        def decorate(f):
            queries = [kak.val['hook_param']] + extra_queries

            flag = '-group ' + group if group else ''
            kak.send('hook', flag, scope, hook_name, repr(filter), "'")
            kak._reentrant_query(f, queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, hook_param, *args):
                return f(ctx, hook_param, *ctx._ask(extra_queries))
            return call_from_python
        return decorate


    def cmd(kak, hidden=True, allow_override=True, pre=None):
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
        ...      ctx.execute('i', y, ':', x, '<esc>')
        >>> kak.evaluate('write_position')
        >>> kak.execute('i,<space><esc>')
        >>> write_position(kak)
        >>> kak.execute('%')
        >>> kak.ask(kak.val.selection)
        '1:1 1:5\\n'
        >>> kak.quit()
        """
        def decorate(f):
            spec = inspect.getfullargspec(f)
            n_as = len(spec.args) - 1
            n_qs = len(spec.defaults)
            if n_as == n_qs:
                raise ValueError('Cannot have a default value for the new context.')
            queries = [kak.arg[i] for i in range(1, n_as - n_qs)]
            queries.extend(spec.defaults)

            flags=['-params ' + str(n_as - n_qs)]
            if hidden:
                flags.append('-hidden')
            if allow_override:
                flags.append('-allow_override')
            if f.__doc__:
                flags.append('-docstring ' + repr(f.__doc__))

            kak.send('def', ' '.join(flags), f.__name__, "'")
            kak.send(single_quote_escape(pre))
            kak.reentrant_query(f, queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, *args):
                if len(args) != n_as - n_qs:
                    raise ValueError('Wrong number of arguments')
                return f(ctx, *(args + ctx.ask(*spec.defaults)))
            return call_from_python
        return decorate


    def on_key(kak, questions=[], before_blocking=None):
        """
        Run on-key and optionally asks questions, get the key and the answers.

        Set the before_blocking callback if you want to do something before
        listening for the key (example: test this function).

        >>> kak = libkak.headless()
        >>> kak.on_key(kak.val.cursor_line,
        ...            before_blocking=lambda: kak.execute('z'))
        ('z', 1)
        >>> kak.quit()
        """
        kak.send('on-key', "'")
        manager = modify_manager(kak.end_quote, post=before_blocking)
        return kak._ask([kak.val['key']] + questions, extra_manager=manager)


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
        return kak._ask([kak.val['text']] + questions, extra_manager=manager)


import time


def stopwatch():
    t0 = time.time()
    return lambda: time.time() - t0


if __name__ == '__main__':
    kak = headless()
    if True:
        t = stopwatch()
        for x in range(100):
            kak.ask(kak.val.cursor_line, kak.val.cursor_column)
        x = t()/100
        print(x, 1/x)
    if True:
        t = stopwatch()
        for x in range(100):
            kak._ask([kak.val.cursor_line], single_query=True)
            kak._ask([kak.val.cursor_column], single_query=True)
        x = t()/100
        print(x, 1/x)
    kak.quit()

    import doctest
    import sys
    sys.exit(0)
    dt_runner = doctest.DebugRunner()
    tests = doctest.DocTestFinder().find(sys.modules[__name__])
    for t in tests:
        t.globs['libkak']=sys.modules[__name__]
        try:
            dt_runner.run(t)
        except doctest.UnexpectedException as e:
            import pdb
            pdb.post_mortem(e.exc_info[2])

