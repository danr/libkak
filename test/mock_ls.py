import sys
import os
sys.path.append(os.getcwd())
import utils
import libkak
import lspc
from multiprocessing import Queue
import json

class MockStdio(object):
    r"""
    A blocking BytesIO

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


def test(debug=False):
    import time
    from threading import Thread

    p, q = Queue(), Queue()

    mock = MockPopen(p, q)
    kak = libkak.headless(ui='dummy' if debug else 'dummy')
    def send(s, sync=False):
        print('Sending:', s)
        libkak.pipe(kak.pid, s, client='unnamed0', sync=sync)

    t = Thread(target=lspc.main, args=(kak.pid, {'mock': mock}))
    t.daemon = True
    t.start()

    time.sleep(1)

    send(""" #kak
    set buffer filetype somefiletype
    declare-option str lsp_servers somefiletype:mock
    lsp_sync
    """)

    print('listening for initalization...')
    obj = process(mock)
    assert(obj['method'] == 'initialize')
    obj = process(mock)
    assert(obj['method'] == 'textDocument/didOpen')
    assert(obj['params']['textDocument']['text'] == '\n')

    print('waiting for hooks to be set up...')
    time.sleep(0.1)
    send(''' # kak
    exec itest.
    hook -group first buffer InsertCompletionShow .* %{
        rmhooks buffer first
        exec <esc>a
        lsp_complete
        hook -group second buffer InsertCompletionShow .* %{
            rmhooks buffer second
            exec '<c-n><esc>\%'
        }
    }
    ''')

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

    send('quit!')
    mock.stdout.closed = True
    kak.wait()


if __name__ == '__main__':
    import doctest
    doctest.testmod()
    test(debug='-v' in sys.argv)


