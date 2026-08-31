#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PatternViewer Local Development & Testing HTTP Server
Serves static files for pattern_viewer and stock reports.
"""

import http.server
import socketserver
import os
import sys
import re
import json
import webbrowser

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_PORT = 8888
# Set root directory to Stock2 project root so /reports/ and /pattern_viewer/ are both accessible
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DIRECTORY = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(DIRECTORY, 'reports')
sys.path.insert(0, DIRECTORY)
from reports_manager_server import read_stock_cards

# Matches e.g. "3034_聯詠(TW).html" or "2059_川湖(TW)(處置期間0810-0814).html"
REPORT_FILENAME_RE = re.compile(r'^(\d{4,6})_(.+?)\((TW|TWO)\)')


def build_reports_index():
    """Walk reports/ recursively and map stock code -> path relative to reports/."""
    index = []
    if not os.path.isdir(REPORTS_DIR):
        return index
    for root, _dirs, files in os.walk(REPORTS_DIR):
        for fname in files:
            if not fname.lower().endswith('.html'):
                continue
            m = REPORT_FILENAME_RE.match(fname)
            if not m:
                continue
            code, name, market = m.groups()
            relpath = os.path.relpath(os.path.join(root, fname), REPORTS_DIR)
            relpath = relpath.replace('\\', '/')
            index.append({'code': code, 'name': name, 'market': market, 'path': relpath})
    return index


class CORSHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        super().end_headers()

    def do_GET(self):
        if self.path == '/api/reports-index':
            payload = json.dumps(build_reports_index()).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == '/api/cards':
            payload = json.dumps(
                {'ok': True, 'cards': read_stock_cards()},
                ensure_ascii=False,
            ).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def run_server():
    os.chdir(DIRECTORY)
    port = DEFAULT_PORT
    httpd = None

    # Try binding to port 8888, fallback to 8889..8899 if busy
    for p in range(DEFAULT_PORT, DEFAULT_PORT + 10):
        try:
            httpd = ReusableTCPServer(("", p), CORSHTTPRequestHandler)
            port = p
            break
        except OSError:
            continue

    if httpd is None:
        url = f"http://localhost:{DEFAULT_PORT}/pattern_viewer/index.html"
        print(f"[PatternViewer] Server is already running. Opening browser at {url}")
        webbrowser.open(url)
        return

    url = f"http://localhost:{port}/pattern_viewer/index.html"
    print("=" * 60)
    print(f" [PatternViewer] Server Started on Port {port}!")
    print(f" URL: {url}")
    print("=" * 60)
    webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)

if __name__ == "__main__":
    run_server()



