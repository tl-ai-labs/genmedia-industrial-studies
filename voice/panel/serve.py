#!/usr/bin/env python3
"""
Serve this folder on http://localhost:8000 and open the panel in a browser.

Only needed if opening index.html directly doesn't work (some browsers block
a local page from loading its clips). Otherwise just double-click index.html.

    python serve.py            # port 8000
    python serve.py 9000       # some other port
"""
import http.server
import os
import socketserver
import sys
import webbrowser

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
os.chdir(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
    url = f"http://localhost:{port}/index.html"
    print(f"serving {os.getcwd()}")
    print(f"open {url}   (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
