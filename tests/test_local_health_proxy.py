from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from jw.langgraph_dev.manager import is_langgraph_dev_running


def test_local_health_ignores_environment_proxy(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/ok" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    try:
        assert is_langgraph_dev_running(port=server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
