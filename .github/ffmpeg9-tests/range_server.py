#!/usr/bin/env python3
"""Static HTTP server with byte ranges for the CI checks of single-file DASH
(http.server of Python ignores Range). usage: range_server.py <port>"""
import http.server
import os
import re
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        self.left = None
        path = self.translate_path(self.path)
        m = re.match(r'bytes=(\d+)-(\d*)$', self.headers.get('Range', ''))
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        first = int(m.group(1))
        last = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        if first > last:
            self.send_error(416)
            return None
        f = open(path, 'rb')
        f.seek(first)
        self.left = last - first + 1
        self.send_response(206)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (first, last, size))
        self.send_header('Content-Length', str(self.left))
        self.end_headers()
        return f

    def end_headers(self):
        if self.left is None:
            self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def copyfile(self, source, outputfile):
        if self.left is None:
            return super().copyfile(source, outputfile)
        outputfile.write(source.read(self.left))

    def log_message(self, fmt, *args):
        pass


http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
