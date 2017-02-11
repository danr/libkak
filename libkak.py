import inspect
import sys
from functools import wraps
from subprocess import Popen


def headless():
    proc = Popen(['kak','-ui','dummy'])
    kak = Kak('pipe', proc.pid)
    kak.to_client('unnamed0')
    return kak


def stdout():
    kak = Kak('stdout')
    kak._session, kak._client = kak.query(kak.val.session, kak.val.client)
    return kak


def connect(pid, client=None)
	kak = Kak('pipe', pid)
	if client:
    	kak.to_client(client)
    return kak


class Kak(object):


    def execute(kak, keys, buffer=None, client=None, try_client=None, draft=False, no_hooks=False, itersel=False, save_regs=None, collapse_jumps=False):
        raise NotImplemented


    def evaluate(kak, keys, buffer=None, client=None, try_client=None, draft=False, no_hooks=False, itersel=False, save_regs=None, collapse_jumps=False):
        raise NotImplemented


    def info(kak, text, anchor=None, placement=None, title=None):
        raise NotImplemented


    def echo(kak, text, color=None, markup=False, debug=False):
        raise NotImplemented


    def set_option(kak, scope, option, value, add=False):
        raise NotImplemented


    def set_register(kak, reg, value):
        raise NotImplemented


    # edit
    # map
    # write
    # call
    # highlighters?


    def __init__(kak, channel, session=None):
        kak._messages = []
        kak._channel  = channel # 'stdout', 'pipe', None or a fifo filename
        kak._session  = session
        kak._client   = None
        kak._main     = kak


    def join(kak):
        """
        Daemonize and wait for all threads to finish
        """
        raise NotImplemented


    def flush(kak):
        chunk = '\n'.join(kak._messages)

        if kak._client:
            chunk = 'eval -client ' + kak._client + '%{' + chunk + '}'

		if not kak._channel:
    		raise ValueError('Need a channel to kak')
        if kak._channel = 'stdout':
            print(chunk)
        elif kak._channel = 'pipe':
            if not kak._session:
                raise ValueError('Cannot pipe to kak without session details')
            Popen(['kak','-p',kak._session], stdout=PIPE).communicate(chunk)
        else:
            with open(kak.channel, 'r') as f:
                f.write(chunk)

        kak._messages=[]
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


    def _setup_query(kak, *queries, end=None):
        fifo = kak._mkfifo()
        # could reuse fifos in the same thread...

        kak.send("%sh{")
        qvars = []
        for i, q in enumerate(queries):
            qvar = "__kak_q"+str(i)
            kak.send(qvar+'=${'+q.variable+'//_/_u}')
            kak.send(qvar+'=${'+qvar+'//\\n/_n}')
            qvars.append('$'+qvar)
        kak.send('echo ' + '_s'.join(qvars) + ' > ' + fifo)
        kak.send(end)
        # kak.send('cat /tmp/ear')
        # kak.channel = '/tmp/ear'

        def _get_answers():
            with open(fifo, 'r') as f:
                response_line = f.readline()
            def unescape(ans):
                return ans.replace('_n','\n').replace('_u','_')
            return tuple(map(unescape, response_line.split('_s')))

        return _get_answers


    def query(kak, *queries, end=None, allow_noop=True):
        if not queries and allow_noop:
            return ()
        else:
            handle = kak._setup_query(queries, end)
            kak.flush()
            # TODO: Need to establish the next communication channel to kak
            return handle()


    def hook(kak, scope, hook_name, filter=r".*", extra_queries=[], group=None):
        """
        Make a kakoune hook.

        Intended to be used as a decorator.

        The decorated function can be called and will execute the same as the
        hook does when triggered.

        >>> kak = libkak.headless()
        >>> kak.hook('global', 'InsertChar')(lambda ctx, c: ctx.exec(':'+str(ord(c))+' '))
        >>> kak.exec('iKak<esc>%')
        >>> kak.query(kak.val.selection) == "K:75 a:97 k:107 \n"
        True
        >>> kak.exec(':q<ret>')
        >>> kak.flush()
        """
        def decorate(f):
            kak.send("hook {flags} {scope} {hook_name} {filter} %{")
            queries = [kak.val['hook_param']] + extra_queries
            handle = kak.setup_query(queries, end="}")

            @kak._fork(lambda ctx: f(ctx, handle()))
            # TODO: for each read line, start a new thread running f on the response

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

        TODO: The *varargs parameter get all (remaining?) arguments.
        """
        def decorate(f):
            spec = inspect.getfullargspec(f)
            n_as = len(spec.args) - 1
            n_qs = len(spec.defaults)
            if n_as == n_qs:
                raise ValueError('Cannot have a default value for the new context.')
            queries = [kak.arg[i] for range(1,n_as - n_qs)]
            queries.extend(spec.defaults)

            kak.send("def -params {n_as - n_qs} {flags} {f.__name__} %{ {pre}".format(...))
            handle = kak._setup_query(*queries, "}")
            # TODO: docstring, params, (completion)

            @kak._fork(lambda ctx: f(ctx, handle()))
            # TODO: for each read line, start a new thread running f on the response

            @wraps(f)
            def call_from_python(ctx, *args):
                if len(args) != n_as - n_qs:
                    raise ValueError('Wrong number of arguments')
                return f(ctx, *(args + ctx.query(*spec.defaults)))
            return call_from_python

        return decorate


    def on_key(kak, *extra_queries):
        """
        Run on-key, returning the pressed key and answers to optional extra queries.
        """
        kak.send("on-key %{"
        return kak.query(kak.val['key'], *extra_queries, end="}")


	def prompt(kak, message, *extra_queries):
    	"""
    	Run prompt, returning the text and answers to optional extra queries.

    	TODO: -init, -password, completion
    	"""
    	kak.send("prompt " + message + " %{")
        return kak.query(kak.val['text'], *extra_queries, end="}")


