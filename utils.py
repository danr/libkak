# -*- coding: utf-8 -*-

from threading import Thread
import six


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


