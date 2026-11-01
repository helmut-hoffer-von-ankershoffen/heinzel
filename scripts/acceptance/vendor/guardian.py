"""Experimental owned-group guardian for README test subprocesses only."""
import os
import selectors
import signal
import struct
import subprocess
import sys
import time

_GUARDIAN = '''import os, signal, struct, subprocess, sys
fd = int(sys.argv[1])
signal.signal(signal.SIGINT, lambda *_: None)
try:
    child = subprocess.Popen(sys.argv[2:])
except OSError:
    os.write(fd, b"E")
else:
    os.write(fd, b"R")
    status = child.wait()
    os.write(fd, b"D" + struct.pack("!i", status))
while True:
    signal.pause()
'''

class Guarded:
    def __init__(self, command, *, cwd, env):
        self.readfd, writer = os.pipe()
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-I', '-c', _GUARDIAN, str(writer), *command], cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(writer,),
            )
        except BaseException:
            os.close(self.readfd)
            raise
        finally:
            os.close(writer)
        os.set_blocking(self.readfd, False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.readfd, selectors.EVENT_READ, 'status')
        self.out = {"stdout": bytearray(), "stderr": bytearray()}
        for name in self.out:
            self.selector.register(getattr(self.process, name), selectors.EVENT_READ, name)
        self.status_buffer = bytearray()
        self.ready = False
        self.status = None

    def pump(self, timeout):
        for iteration in range(16):
            for key, _ in self.selector.select(timeout if iteration == 0 else 0):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    if key.data == 'status':
                        raise RuntimeError('guardian exited before cleanup; group status uncertain')
                    self.selector.unregister(key.fileobj)
                elif key.data == 'status':
                    self.status_buffer.extend(chunk)
                    if len(self.status_buffer) > 6:
                        raise RuntimeError('invalid guardian status')
                else:
                    tail = self.out[key.data]
                    tail.extend(chunk)
                    del tail[:-65536]
            if self.status_buffer and not self.ready:
                if self.status_buffer[0] != ord('R'):
                    raise RuntimeError('guardian could not start command')
                del self.status_buffer[:1]
                self.ready = True
            if len(self.status_buffer) >= 5:
                if self.status_buffer[0] != ord('D'):
                    raise RuntimeError('invalid guardian status')
                self.status = struct.unpack('!i', self.status_buffer[1:5])[0]
                del self.status_buffer[:5]
            if not self.selector.get_map():
                break

    def alive(self):
        return os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.status is None:
            if not self.alive():
                raise RuntimeError('guardian exited before cleanup; group status uncertain')
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise subprocess.TimeoutExpired(self.process.args, timeout)
            self.pump(min(remain, 0.05))
        return self.status

    def stop(self):
        try:
            if not self.alive():
                self.process.wait(timeout=1)
                raise RuntimeError('guardian exited before cleanup; group status uncertain')
            status_error = None
            try:
                self.pump(0)
            except RuntimeError as error:
                status_error = error
            if status_error is None and self.ready and self.status is None:
                os.killpg(self.process.pid, signal.SIGINT)
                try:
                    self.wait(10)
                except subprocess.TimeoutExpired:
                    pass
            if not self.alive():
                self.process.wait(timeout=1)
                raise RuntimeError('guardian exited before cleanup; group status uncertain')
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
            self.selector.unregister(self.readfd)
            self.pump(0)
            if status_error is not None:
                raise status_error
        finally:
            self.selector.close()
            os.close(self.readfd)
            for stream in (self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()
