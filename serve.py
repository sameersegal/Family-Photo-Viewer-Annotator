"""
Local development server for Family Photo Album.

Serves the app at http://localhost:8765 with proper MIME types
for ES modules and JSON files.

Usage:
  python serve.py
  python serve.py 3000    # Custom port
"""

import http.server
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

# Ensure proper MIME types for ES modules
http.server.SimpleHTTPRequestHandler.extensions_map.update({
    '.js': 'application/javascript',
    '.mjs': 'application/javascript',
    '.json': 'application/json',
    '.css': 'text/css',
    '.html': 'text/html',
    '.webp': 'image/webp',
})


class AlbumHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Serve index.html for the root path
        if self.path == '/' or self.path == '':
            self.path = '/index.html'
        return super().do_GET()

    def end_headers(self):
        # Allow ES module imports and Firebase CDN
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def log_message(self, format, *args):
        # Quieter logging — only show errors and served files
        if args and '404' in str(args[1]) if len(args) > 1 else False:
            super().log_message(format, *args)


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


with ThreadedServer(("", PORT), AlbumHandler) as httpd:
    print(f"Family Photo Album running at http://localhost:{PORT}")
    print(f"Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
