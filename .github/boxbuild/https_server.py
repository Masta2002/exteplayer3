#!/usr/bin/env python3
"""Static HTTPS server for the TLS check of the ARM build.
usage: https_server.py <directory> <port> <cert.pem> <key.pem>"""
import functools
import http.server
import ssl
import sys

directory, port, cert, key = sys.argv[1:5]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
server = http.server.ThreadingHTTPServer(('127.0.0.1', int(port)), handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
server.serve_forever()
