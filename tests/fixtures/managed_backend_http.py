"""Owned-descriptor HTTP worker for native lifecycle tests; no external networking or app permissions."""

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

fd, token = os.environ["HEINZEL_UI_RESUME"].split(":")
sock = socket.socket(fileno=int(fd))
instance = os.urandom(16).hex()
APP_HTML = (
    b"<!doctype html><html><head><title>Fixture</title></head><body>verified fixture app"
    b'<pre id="state">Loading</pre><script>window.hzDesktop.snapshot().then(value => '
    b'document.querySelector("#state").textContent = JSON.stringify(value));</script></body></html>'
)
SERVICE_WORKER = b"""
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (new URL(event.request.url).pathname !== '/assets/cache.js') return;
  event.respondWith(caches.open('hz-native-fixture').then(async cache => {
    const cached = await cache.match(event.request);
    if (cached) {
      return new Response(cached.body, {headers: {'Content-Type': 'application/javascript', 'X-Fixture-Cache': 'hit'}});
    }
    const response = await fetch(event.request);
    await cache.put(event.request, response.clone());
    return response;
  }));
});
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/api/lifecycle":
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"instance": instance, "version": "0.7.12", "pid": os.getpid()}).encode()
            content_type = "application/json"
        elif self.path == "/sw.js":
            body = SERVICE_WORKER
            content_type = "application/javascript"
        elif self.path == "/assets/cache.js":
            body = b"window.fixtureCacheVisits = (window.fixtureCacheVisits || 0) + 1;"
            content_type = "application/javascript"
        else:
            body = APP_HTML
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=False)
server.socket.close()
server.socket = sock
server.server_address = sock.getsockname()


def parent_closed():
    sys.stdin.buffer.read(1)
    os._exit(0)


threading.Thread(target=parent_closed, daemon=True).start()
server.serve_forever()
