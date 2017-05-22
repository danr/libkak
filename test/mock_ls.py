from __future__ import print_function
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
            self.q.put(chr(c) if isinstance(c, int) else c)

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
    """
    Listen for a message to the mock process and make a standard
    reply or return result if it is not None.
    """
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
    msg = utils.jsonrpc({
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
        lsp-sync # why does it not trigger on WinSetOption?
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
    send('lsp-hover docsclient')
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
                send("exec '<a-;>:lsp-signature-help docsclient<ret>'", sync=False)
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
    q = Queue()

    @libkak.Remote.hook(kak.pid, 'buffer', 'InsertCompletionShow',
                        client='unnamed0', sync_setup=True)
    def hook(pipe):
        pipe("exec '<c-n><esc>\%'")
        q.put(())
    send('exec itest.')

    print('listening...')
    obj = process(mock)
    pprint(obj)
    assert(obj['method'] == 'textDocument/didChange')
    assert(obj['params']['contentChanges'][0]['text'] == 'test.\n')
    obj = process(mock)
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})
    q.get()
    call = libkak.Remote.onclient(kak.pid, 'unnamed0')
    s = call(lambda selection: selection)
    print('final selection:', s)
    assert(s == 'test.apa\n')


@setup_test
def test_diagnostics(kak, mock, send):
    send('exec 7oabcdefghijklmnopqrstuvwxyz<esc>gg')

    msg = utils.jsonrpc({
        'method': 'textDocument/publishDiagnostics',
        'params': {
            'uri': 'file://*scratch*',
            'diagnostics': [{
                'message': 'line ' + str(y),
                'range': {
                    'start': {
                        'line': y-1,
                        'character': y*2-1
                    },
                    'end': {
                        'line': y-1,
                        'character': y*3-1
                    }
                }
            } for y in [2, 4, 6]]
        }
    })
    mock.stdout.write(msg)
    time.sleep(0.1)
    first = True
    for y in [2,4,6,2,4]:
        send('lsp-diagnostics-jump next')  # 2 4 6 2 4
        if first:
            first = False
            print('listening...')
            obj = process(mock)
            pprint(obj)
            assert(obj['method'] == 'textDocument/didChange')
            assert(obj['params']['contentChanges'][0]['text'] == '\n' + 'abcdefghijklmnopqrstuvwxyz\n' * 7)
        time.sleep(0.1)
        call = libkak.Remote.onclient(kak.pid, 'unnamed0')
        d = call(lambda selection_desc: selection_desc)
        print('selection_desc:', d)
        assert(d == ((y,2*y),(y,3*y-1)))  # end point exclusive according to protocol.md

    send('lsp-diagnostics-jump prev')  # 2
    time.sleep(0.1)
    send('lsp-diagnostics docsclient')
    time.sleep(0.3)
    send('exec x')
    time.sleep(0.3)
    call = libkak.Remote.onclient(kak.pid, 'unnamed0')
    s = call(lambda selection: selection)
    print('final selection:', s)
    assert(s == 'line 2\n')


if __name__ == '__main__':
    import doctest
    doctest.testmod()
    debug = '-v' in sys.argv
    test_completion(debug)
    test_sighelp(debug)
    test_hover(debug)
    test_diagnostics(debug)
