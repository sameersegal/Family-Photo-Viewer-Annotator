import http.server
import socketserver

PORT = 8765

class SlideshowHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '':
            self.path = '/slideshow.html'
        return super().do_GET()

    def log_message(self, format, *args):
        pass  # suppress noisy logs

class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

with ThreadedServer(("", PORT), SlideshowHandler) as httpd:
    print(f"Serving at http://localhost:{PORT}")
    httpd.serve_forever()
