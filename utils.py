# -*- coding: utf-8 -*-

from threading import Thread
import six
import inspect
import json


def range(r):
    y0 = int(r['start']['line']) + 1
    x0 = int(r['start']['character']) + 1
    y1 = int(r['end']['line']) + 1
    x1 = int(r['end']['character'])
    return ((y0, x0), (y1, x1))


def jsonrpc(obj):
    obj['jsonrpc'] = '2.0'
    msg = json.dumps(obj)
    msg = u"Content-Length: {0}\r\n\r\n{1}".format(len(msg), msg)
    return msg.encode('utf-8')


def deindent(s):
    """
    >>> print(deindent('''
    ...       bepa
    ...     apa
    ...
    ...      cepa
    ...    '''))
    <BLANKLINE>
      bepa
    apa
    <BLANKLINE>
     cepa
    <BLANKLINE>
    """
    lines = s.split('\n')

    chop = 98765
    for line in lines:
        if line.strip():
            m = len(line) - len(line.lstrip())
            if m < chop:
                chop = m

    return '\n'.join(line[chop:] for line in lines)


def fork(loop=False):
    def decorate(f):
        def target():
            try:
                while True:
                    f()
                    if not loop:
                        break
            except RuntimeError:
                pass
        thread = Thread(target=target)
        thread.daemonize = True
        thread.start()
    return decorate


def join(words, sep=u' '):
    """
    Join strings or bytes into a string, returning a string.
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


def single_quote_escape(string):
    """
    Backslash-escape ' and \ in Kakoune style .
    """
    return string.replace("\\'", "\\\\'").replace("'", "\\'")


def single_quoted(string):
    u"""
    The string wrapped in single quotes and escaped in Kakoune style.

    https://github.com/mawww/kakoune/issues/1049

    >>> print(single_quoted(u"i'ié"))
    'i\\'ié'
    """
    return u"'" + single_quote_escape(string) + u"'"


def backslash_escape(cs, s):
    for c in cs:
        s = s.replace(c, "\\" + c)
    return s


def argnames(f):
    """
    >>> argnames(lambda x, y, *zs, **kws: None)
    ['x', 'y']
    """
    return inspect.getargspec(f).args


def safe_kwcall(f, d):
    """
    >>> safe_kwcall(lambda x: x, dict(x=2, y=3))
    2
    """
    return f(*(d[k] for k in argnames(f)))
