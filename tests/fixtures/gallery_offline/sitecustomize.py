"""Deny Python network clients in the synthetic gallery backend and its Python children.

This is a test fixture, not an OS security boundary. The backend does not need
outbound IP connections; the browser connects to it from a separate process.
"""

import os
import pathlib
import socket


def denied(*_args, **_kwargs):
    with pathlib.Path(os.environ["HZ_GALLERY_DENIAL_LOG"]).open("a", encoding="utf-8") as log:
        log.write("blocked outbound request\n")
    msg = "gallery fixture denies outbound network"
    raise PermissionError(msg)


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_sendto = socket.socket.sendto


def connect(sock, address):
    return denied() if sock.family in (socket.AF_INET, socket.AF_INET6) else _connect(sock, address)


def connect_ex(sock, address):
    return denied() if sock.family in (socket.AF_INET, socket.AF_INET6) else _connect_ex(sock, address)


def sendto(sock, *args):
    return denied() if sock.family in (socket.AF_INET, socket.AF_INET6) else _sendto(sock, *args)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.sendto = sendto
socket.getaddrinfo = denied

with pathlib.Path(os.environ["HZ_GALLERY_INITIALIZED"]).open("w", encoding="utf-8") as marker:
    marker.write("gallery network denial active\n")
