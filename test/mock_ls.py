import sys
import os
sys.path.append(os.getcwd())
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
        return libkak.encode(''.join(cs))

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


def test(debug=False):
    import time
    from threading import Thread

    p, q = Queue(), Queue()

    lsp_mock = MockPopen(p, q)
    kak = libkak.headless(debug=debug, ui='json' if debug else 'dummy')
    kak.send('declare-option str filetype somefiletype')
    kak.send('declare-option str lsp_somefiletype_cmd mock')
    kak.send('set global completers option=lsp_completions')
    kak.sync()
    kak2 = libkak.Kak('pipe', kak._pid, 'unnamed0', debug=debug)
    t = Thread(target=lspc.main, args=(kak2, {'mock': lsp_mock}))
    t.daemon = True
    t.start()

    kak.release()

    def listen():
        line = lsp_mock.stdin.readline()
        header, value = line.split(b":")
        assert(header == b"Content-Length")
        cl = int(value)
        lsp_mock.stdin.readline()
        obj = json.loads(lsp_mock.stdin.read(cl).decode('utf-8'))
        print('heard:', json.dumps(obj, indent=2))
        return obj

    def reply(obj, result):
        lsp_mock.stdout.write(lspc.jsonrpc({
            'id': obj['id'],
            'result': result
        }))


    print('listening for initalization...')
    obj = listen()
    assert(obj['method'] == 'initialize')
    reply(obj, {
        'capabilities': {
            'signatureHelpProvider': {
                'triggerCharacters': ['(', ',']
            },
            'completionProvider': {
                'triggerCharacters': ['.']
            }
        }
    })
    time.sleep(1)  # wait for triggers and definition to be set up
    kak.execute('itest.')
    kak.release()
    print('listening for hook on completion...')
    obj = listen()
    assert(obj['method'] == 'textDocument/didOpen')
    #assert(obj['params']['textDocument']['text'] == 'test.\n')
    reply(obj, None)
    obj = listen()
    assert(obj['method'] == 'textDocument/didChange')
    assert(obj['params']['contentChanges'][0]['text'] == 'test.\n')
    reply(obj, None)
    obj = listen()
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})
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
    reply(obj, {'items': items})

    time.sleep(1)
    kak.execute('<esc>a')
    kak.send('lsp_complete ""')
    kak.release()
    obj = listen()
    assert(obj['method'] == 'textDocument/completion')
    assert(obj['params']['position'] == {'line': 0, 'character': 5})
    reply(obj, {'items': items})

    time.sleep(1)
    kak.execute('<c-n><c-n><esc>%')
    kak.sync()
    s = kak.val.selection()
    print('final selection:', s)
    assert(s == 'test.bepa\n')

    kak.quit(force=True)
    lsp_mock.stdout.closed = True


if __name__ == '__main__':
    import doctest
    doctest.testmod()
    test(debug='-v' in sys.argv)


