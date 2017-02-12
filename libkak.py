import inspect
import sys
from functools import wraps
from subprocess import Popen
from contextlib import contextmanager


def headless():
    proc = Popen(['kak','-n','-ui','dummy'])
    kak = Kak('pipe', proc.pid, 'unnamed0')
    kak.query(allow_noop=False)
    return kak


def unconnected():
    return Kak(None)


@contextmanager
def modify_manager(manager, pre=None, post=None):
    if pre:
        pre()
    with manager() as a:
        yield a
    if post:
        post()


@contextmanager
def nest_managers(outer, inner):
    if outer is None:
        with inner() as a:
            yield a
    else:
        with outer(), inner() as a, b:
            yield a, b


def escape(string, *chars_to_replace, escape_char='\\'):
    """
    Escaping, defaults to backslash escaping.

    The chars are replaced in argument order.

    >>> escape('abc', 'a', 'b', escape_char='a')
    'aaabc'

    >>> escape('abc', 'b', 'a', escape_char='a')
    'aaaabc'
    """
    for c in chars_to_replace:
        string = string.replace(c, '\\'+c)
    return string


def single_quote_escape(string):
    """
    Backslash-escape ' and \.

    >>> print(single_quote_escape("'\\'"))
    \'\\\'
    """
    return escape(string, '\\', "'")


def replace(string, *replacement_tuples):
    """
    Perform many str.replace replacements.

    >> replace('_n_ub', ('_n', '\n'), ('_u', ''))
    '\n_b'
    """
    for s, t in replacement_tuples:
        string = string.replace(s, t)
    return string


class Kak(object):


    def execute(kak, *keys, buffer=None, client=None, try_client=None, draft=False, no_hooks=False, itersel=False, save_regs=None, collapse_jumps=False):
        """
        TODO: test escaping, support flags
        """
        kak.send("exec '" + single_quote_escape(''.join(keys)) + "'")


    def evaluate(kak, *cmds, buffer=None, client=None, try_client=None, draft=False, no_hooks=False, itersel=False, save_regs=None, collapse_jumps=False):
        """
        TODO: test escaping, support flags
        """
        kak.send("eval '")
        with kak.end_quote():
            for cmd in cmds:
                kak.send(cmd)


    def info(kak, *text, anchor=None, placement=None, title=None):
        """
        TODO: test escaping, support flags
        """
        kak.send("info '" + single_quote_escape(' '.join(text)) + "'")


    def echo(kak, *text, color=None, markup=False, debug=False):
        """
        TODO: test escaping, support flags
        """
        kak.send("echo '" + single_quote_escape(' '.join(text)) + "'")


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


    def send(kak, *words):
        """
        Send a raw message to kak.

        This is buffered and all messages are transmitted in one go.
        """
        kak._messages.append(' '.join(words))


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
            kak._session, kak._client = kak.query(kak.val.session, kak.val.client)
        kak._flush()
        kak._channel = 'pipe'


    def debug_sent(kak):
        return '\n'.join(kak._messages)


    def _flush(kak, soft=False):
        """
        Flush everything that has been sent.

        This is probably not the function you are looking for.
        Perhaps you want ``release``, ``query`` or ``join``?

        Soft means writing what we have so far if communicating via stdout
        and a noop when writing anywhere else.
        """
        if soft and kak._channel != 'stdout':
            return

        chunk = '\n'.join(kak._messages) + '\n'

        if kak._client and kak._channel = 'pipe':
            chunk = 'eval -client ' + kak._client + "'\n" + single_quote_escape(chunk) + "\n'"

		if not kak._channel:
    		raise ValueError('Need a channel to kak')
        if kak._channel = 'stdout':
            print(chunk)
        elif kak._channel = 'pipe':
            if not kak._session:
                raise ValueError('Cannot pipe to kak without session details')
            Popen(['kak','-p',kak._session], stdout=PIPE).communicate(chunk)
        else:
            with open(kak._channel, 'r') as f:
                f.write(chunk)

        kak._messages=[]
        if not soft:
            kak._channel=None


    def to_client(kak, client):
        kak.client = client


    def _mkfifo(kak):
        raise NotImplemented


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
          echo echo \'$PWD=\'"$PWD"
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
        parent = kak.send
        kak.send = lambda *words: parent(' ', *map(word_modifier, words))
        yield
        kak.send = parent


    def _setup_query(kak, queries, extra_manager=None):
        from_kak = kak._mkfifo()

        with nest_managers(extra_manager, kak.sh):
            kak.send('__to_dir=$(mktemp -d)')
            kak.send('__to_kak=$__to_dir/to_kak'
            kak.send('mkfifo $__to_kak')
            qvars = ['$__to_kak']
            for i, q in enumerate(queries):
                qvar = "__kak_q"+str(i)
                kak.send(qvar+'=${'+q.variable+'//_/_u}')
                kak.send(qvar+'=${'+qvar+'//\\n/_n}')
                qvars.append('$'+qvar)
            kak.send('echo', '_s'.join(qvars), '>', from_kak)
            kak.send('cat', to_kak)
            kak.send('rm $__to_kak')
            kak.send('rmdir $__to_dir')

        def handle():
            with open(from_kak, 'r') as f:
                response_line = f.readline()
            answers = tuple(replace(ans, ('_n', '\n'), ('_u', ''))
                            for ans in response_line.split('_s'))
                            # TODO: parsing

                 # from_kak, to_kak,     *query answers
            return from_kak, answers[0], answers[1:]

        return handle


    def query(kak, *queries, extra_manager=None, allow_noop=True):
        """
        Make queries and return the answers.

        Blocks the python thread until the answers have arrived.
        (Unless allow_noop is True (default) and the query list is empty.)

        Blocks kak until we write some response (``query``, ``release``, etc).
        This means that if we are connecting to a running kak process,
        from now on it starts blocking.

        >>> kak = libkak.headless()
        >>> kak.query(kak.val.cursor_line)
        1
        >>> kak.quit()
        """
        if not queries and allow_noop:
            if extra_manager:
                with extra_manager():
                    return ()
            else:
                return ()
        else:
            handle = kak._setup_query(queries, extra_manager=extra_manager)
            kak._flush()
            from_kak, to_kak, answers = handle()
            os.rmfile(from_kak)
            kak._channel = to_kak
            return answers


    def reentrant_query(kak, f, *queries, extra_manager=None):
        """
        Make a query, but listen for answers forever and respond by calling f.

        Used for responding indefinitely to define-command calls and hooks.
        """
        handle = kak._setup_query(queries, extra_manager=extra_manager)
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
        ... def ascii(ctx, char):
        ...     ctx.execute(':', str(ord(char)), '<space>')
        >>> kak.execute('iKak<esc>%')
        >>> kak.query(kak.val.selection)
        'K:75 a:97 k:107 \n'
        >>> kak.execute('di')
        >>> ascii(kak, 'A')
        >>> kak.execute('<esc>%')
        >>> kak.query(kak.val.selection)
        'A:65 \n'
        >>> kak.quit()
        """
        def decorate(f):
            queries = [kak.val['hook_param']] + extra_queries

            flag = '-group ' + group if group else ''
            kak.send('hook', flag, scope, hook_name, repr(filter), "'")
            kak.reentrant_query(f, *queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, hook_param, *args):
                return f(ctx, hook_param, *ctx.query(*extra_queries))
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
        >>> kak.query(kak.val.selection) == '1:1 1:5\n'
        True
        >>> kak.quit()
        """
        def decorate(f):
            spec = inspect.getfullargspec(f)
            n_as = len(spec.args) - 1
            n_qs = len(spec.defaults)
            if n_as == n_qs:
                raise ValueError('Cannot have a default value for the new context.')
            queries = [kak.arg[i] for range(1,n_as - n_qs)]
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
            kak.reentrant_query(f, *queries, extra_manager=kak.end_quote)

            @wraps(f)
            def call_from_python(ctx, *args):
                if len(args) != n_as - n_qs:
                    raise ValueError('Wrong number of arguments')
                return f(ctx, *(args + ctx.query(*spec.defaults)))
            return call_from_python
        return decorate


    def on_key(kak, *extra_queries, before_blocking=None):
        """
        Run on-key, returning the pressed key and answers to optional extra
        queries.

        Set the before_blocking callback if you want to do something before
        listening for the key.

        >>> libkak = kak.headless()
        >>> kak.on_key(kak.val.cursor_line,
        ...            before_blocking=lambda: kak.execute('z'))
        ('z', 1)
        >>> kak.quit()
        """
        kak.send('on-key', "'")
        manager = modify_manager(kak.end_quote, post=before_blocking)
        return kak.query(kak.val['key'], *extra_queries, extra_manager=manager)


    def prompt(kak, message, *extra_queries, init=None):
    	"""
    	Run prompt, returning the text and answers to optional extra queries.

    	Not implemented: -init
    	"""
    	flag = '-init ' + init if init else ''
    	kak.send('prompt', flag, repr(message), "'")
        return kak.query(kak.val['text'], *extra_queries, end=kak.end_quote)

