# Copyright (c) 2009-2012, Dmitry Vasiliev <dima@hlabs.org>
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
#  * Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#  * Neither the name of the copyright holders nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission. 
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import sys
from sys import exc_info
from threading import Lock
from traceback import extract_tb

from erlport import Atom

class Error(Exception):
    """ErlPort Error."""

class InvalidMessage(Error):
    """Invalid message."""

class UnknownMessage(Error):
    """Unknown message."""

class InvalidMode(Error):
    """Invalid mode."""

class ErlangError(Error):
    """Erlang error()."""

    def __init__(self, type, value, stacktrace):
        self.type = type
        self.value = value
        self.stacktrace = stacktrace
        Error.__init__(self, (type, value, stacktrace))


class MessageHandler(object):

    def __init__(self, port):
        self.port = port
        self.client = False
        self.set_encoder(None)
        self.set_decoder(None)
        call_lock = Lock()
        self._call_lock_acquire = call_lock.acquire
        self._call_lock_release = call_lock.release

    def set_encoder(self, encoder):
        if encoder:
            self.encoder = self.object_iterator(encoder)
        else:
            self.encoder = lambda o: o

    def set_decoder(self, decoder):
        if decoder:
            self.decoder = self.object_iterator(decoder)
        else:
            self.decoder = lambda o: o

    def object_iterator(self, handler,
            isinstance=isinstance, list=list, tuple=tuple, map=map):
        def iterator(obj):
            obj = handler(obj)
            if isinstance(obj, (list, tuple)):
                return obj.__class__(map(iterator, obj))
            return obj
        return iterator

    def start(self):
        call = self.call_with_error_handler
        try:
            self.loop(self.port.read, self.port.write, call)
        except EOFError:
            pass

    def loop(self, read, write, call):
        switch_ack = Atom(b"s")
        while True:
            message = read()
            try:
                mtype, module, function, args = message
            except ValueError:
                raise InvalidMessage(message)

            if mtype == b"C":
                write(call(module, function, args))
            elif mtype == b"S":
                write(switch_ack)
                self.client = True
                write(call(module, function, args))
                self.client = False
            else:
                raise UnknownMessage(message)

    def call(self, module, function, args):
        if not self.client:
            raise InvalidMode("call() is unsupported in server mode")

        if not isinstance(module, Atom):
            raise ValueError(module)
        if not isinstance(function, Atom):
            raise ValueError(function)
        if not isinstance(args, list):
            raise ValueError(args)

        self._call_lock_acquire()
        try:
            self.port.write((Atom(b"C"), module, function,
                # TODO: Optimize list(map())
                list(map(self.encoder, args))))
            response = self.port.read()
        finally:
            self._call_lock_release()
        try:
            mtype, value = response
        except ValueError:
            raise InvalidMessage(response)

        if mtype != b"r":
            if mtype == b"e":
                # TODO: Raise error based on error value
                raise Exception("error")
            raise UnknownMessage(response)
        return self.decoder(value)

    def call_with_error_handler(self, module, function, args):
        try:
            mod = module.decode()
            objects = function.decode().split(".")
            f = sys.modules.get(mod)
            if not f:
                f = __import__(mod, {}, {}, [objects[0]])
            for o in objects:
                f = getattr(f, o)
            result = Atom(b"r"), self.encoder(f(*map(self.decoder, args)))
        except:
            # TODO: Update exception format
            t, val, tb = exc_info()
            exc = Atom(bytes("%s.%s" % (t.__module__, t.__name__), "utf-8"))
            exc_tb = extract_tb(tb)
            exc_tb.reverse()
            result = Atom(b"e"), (exc, str(val), exc_tb)
        return result

class Function(object):

    __slots__ = ()

    def __new__(cls, name, module):
        cls.__call__ = lambda s, *args: call(module, name, list(args))
        return super(Function, cls).__new__(cls)

class Module(object):

    __slots__ = ()

    def __new__(cls, name):
        cls.__getattribute__ = lambda s, fname: Function(
            Atom(fname.encode()), name)
        return super(Module, cls).__new__(cls)

class Erlang(object):

    __slots__ = ()

    def __getattribute__(self, module):
        return Module(Atom(module.encode()))

Erlang = Erlang()


def setup(port):
    global MessageHandler, setup, call, set_encoder, set_decoder
    handler = MessageHandler(port)
    call = handler.call
    set_encoder = handler.set_encoder
    set_decoder = handler.set_decoder
    del MessageHandler, setup
    handler.start()