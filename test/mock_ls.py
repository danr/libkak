import sys
import os
sys.path.append(os.getcwd())
from multiprocessing import Queue
from pprint import pprint
from threading import Thread
import json
import libkak
import lspc
import subprocess
import time
import utils


class MockStdio(object):
    r"""
    A blocking BytesIO.

    >>> io = MockStdio(Queue())
    >>> io.write('abc\n')
    >>> print(io.read(2).decode('utf-8'))
    ab
    >>> print(io.readline().decode('utf-8'))
    c
    <BLANKLINE>
    >>> io.closed = True
    >>> print(io.readline().decode('utf-8'))
    <BLANKLINE>
    """
    def __init__(self, q):
        self.q = q
        self.closed = False

    def write(self, msg):
        for c in msg:
            self.q.put(chr(c) if isinstance(c,int) else c)

    def flush(self):
        pass

    def read(self, n):
        cs = []
        for _ in range(n):
            while not self.closed:
                try:
                    c = self.q.get(timeout=1)
                    break
                except:
                    pass
            if self.closed:
                break
            cs.append(c)
        return utils.encode(''.join(cs))

    def readline(self):
        cs = []
        while True:
            if self.closed:
                break
            c = self.read(1)
            cs.append(c)
            if c == b'\n':
                break
        return b''.join(cs)


class MockPopen(object):
    def __init__(self, q_in, q_out):
        self.stdin = MockStdio(q_in)
        self.stdout = MockStdio(q_out)


def listen(p):
    line = p.stdin.readline()
    header, value = line.split(b":")
    assert(header == b"Content-Length")
    cl = int(value)
    p.stdin.readline()
    obj = json.loads(p.stdin.read(cl).decode('utf-8'))
    print('Server received: ', json.dumps(obj, indent=2))
    return obj


def process(mock, result=None):
    obj = listen(mock)
    method = obj['method']
    if result:
        pass
    elif method == 'initialize':
        result = {
            'capabilities': {
                'signatureHelpProvider': {
                    'triggerCharacters': ['(', ',']
                },
                'completionProvider': {
                    'triggerCharacters': ['.']
                }
            }
        }
    elif method in ['textDocument/didOpen', 'textDocument/didChange']:
        result = None
    elif method == 'textDocument/hover':
        result = {
            'contents': ['example hover text']
        }
    elif method == 'textDocument/signatureHelp':
        result = {
            'signatures': [
                {
                    'parameters': [
                        {'label': 'first: int'},
                        {'label': 'second: str'},
                    ],
                    'label': 'example_function(...): boolean'
                }
            ],
            'activeSignature': 0,
            'activeParameter': 0
        }
    elif method == 'textDocument/completion':
        items = [
            {
                'label': 'apa',
                'kind': 3,
                'documentation': 'monkey function',
                'detail': 'call the monkey',
            },
            {
                'label': 'bepa',
                'kind': 4,
                'documentation': 'monkey constructor',
                'detail': 'construct a monkey',
            }
        ]
        result = {'items': items}
    else:
        raise RuntimeError('Unknown method: ' + method)
    msg = lspc.jsonrpc({
        'id': obj['id'],
        'result': result
    })
    mock.stdout.write(msg)
    return obj


def setup_test(f):
    def decorated(debug=False):
        p, q = Queue(), Queue()

        mock = MockPopen(p, q)
        kak = libkak.headless(ui='json' if debug else 'dummy',
                              stdout=subprocess.PIPE)
        @utils.fork(loop=True)
        def json_ui_monitor():
            try:
                obj = json.loads(kak.stdout.readline())
                pprint(obj)
            except:
                raise RuntimeError

        def send(s, sync=False):
            print('Sending:', s)
            libkak.pipe(kak.pid, s, client='unnamed0', sync=sync)

        t = Thread(target=lspc.main, args=(kak.pid, {'mock': mock}))
        t.daemon = True
        t.start()

        time.sleep(0.25)

        send(""" #kak
        declare-option str docsclient
        set window filetype somefiletype
        declare-option str lsp_servers somefiletype:mock
        lsp_sync # why does it not trigger on WinSetOption?
        """)


        print('listening for initalization...')
        obj = process(mock)
        assert(obj['method'] == 'initialize')
        obj = process(mock)
        assert(obj['method'] == 'textDocument/didOpen')
        assert(obj['params']['textDocument']['text'] == '\n')

        print('waiting for hooks to be set up...')
        time.sleep(0.1)

        f(kak, mock, send)

        send('quit!')
        mock.stdout.closed = True
        libkak._fifo_cleanup()
        kak.wait()

    return decorated


@setup_test
def test_hover(kak, mock, send):
    send('lsp_hover docsclient')
    while True:
        obj = process(mock)
        if obj['method'] == 'textDocument/hover':
            break
    time.sleep(0.1)
    send('exec \%', sync=True)
    call = libkak.Remote.onclient(kak.pid, 'unnamed0')
    s = call(lambda selection: selection)
    print('hover text:', s)
    assert(s == 'example hover text\n')


@setup_test
def test_sighelp(kak, mock, send):
    send('exec iexample_function(', sync=False)
    c = 0
    while True:
        obj = process(mock)
        if obj['method'] == 'textDocument/signatureHelp':
            c += 1
            if c == 1:
                # good, triggered correctly, now onto docsclient
                send("exec '<a-;>:lsp_signature_help docsclient<ret>'", sync=False)
            if c == 2:
                break
    time.sleep(0.1)
    send('exec \%', sync=True)
    call = libkak.Remote.onclient(kak.pid, 'unnamed0')
    s = call(lambda selection: selection)
    print('sighelp:', s)
    assert(s == 'example_function(*first: int*, second: str): boolean\n')


@setup_test
def test_completion(kak, mock, send):
    d = dict(count = 0)
    @libkak.Remote.hook(kak.pid, 'buffer', 'InsertCompletionShow',
                        client='unnamed0', sync_setup=True)
    def hook(reply):
        print('count:', d['count'])
        time.sleep(0.1)
        if d['count'] == 0:
            reply('exec <esc>a; lsp_complete')
        elif d['count'] == 1:
            reply("exec '<c-n><esc>\%'")
        d['count'] += 1
    send('exec itest.')

    print('listening...')
    obj = process(mock)
    assert(obj['method'] == 'textDocument/didChange')
    assert(obj['params']['contentChanges'][0]['text'] == 'test.\n')
    obj = process(mock)
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})
    # here comes second...
    items = [{'label': 'bepus'}]
    obj = process(mock, {'items': items})
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})

    print('waiting for hooks to be triggered')
    time.sleep(0.1)
    call = libkak.Remote.onclient(kak.pid, 'unnamed0')
    s = call(lambda selection: selection)
    print('final selection:', s)
    assert(s == 'test.bepus\n')


if __name__ == '__main__':
    import doctest
    doctest.testmod()
    debug = '-v' in sys.argv
    test_completion(debug)
    test_sighelp(debug)
    test_hover(debug)


