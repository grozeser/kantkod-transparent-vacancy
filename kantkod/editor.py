import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analysis import TOPICS
from .preview import preview_vacancy
from .source import read_json
from .storage import database, read_editor, set_status


STATIC = Path(__file__).parent / "static"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def serve_editor(report_path, db_path, port):
    channel = str(report_path.resolve())

    class EditorHandler(BaseHTTPRequestHandler):
        def send_bytes(self, status, media_type, body):
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, payload):
            self.send_bytes(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode())

        def valid_host(self):
            listen_port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{listen_port}", f"localhost:{listen_port}"}

        def do_GET(self):
            if not self.valid_host():
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Откройте редактор по локальному адресу"})
                return
            if self.path in FILES:
                name, media_type = FILES[self.path]
                self.send_bytes(HTTPStatus.OK, media_type, (STATIC / name).read_bytes())
                return
            if self.path == "/favicon.ico":
                self.send_bytes(HTTPStatus.NO_CONTENT, "text/plain", b"")
                return
            if self.path != "/data":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "Страница не найдена"})
                return
            try:
                report = read_json(report_path) if report_path.is_file() else None
                with database(db_path) as connection:
                    states, attempt = read_editor(connection, channel)
                self.send_json(HTTPStatus.OK, {"report": report, "states": states, "attempt": attempt, "topics": TOPICS})
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_POST(self):
            if self.path not in {"/status", "/preview"}:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "Действие не найдено"})
                return
            origin = self.headers.get("Origin")
            if (not self.valid_host() or origin != f"http://{self.headers.get('Host')}"
                    or self.headers.get_content_type() != "application/json"):
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Запрос разрешён из локального редактора"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 16_384:
                    raise ValueError("Некорректный размер запроса")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Некорректные данные запроса")
                with database(db_path) as connection:
                    if self.path == "/preview":
                        result = preview_vacancy(connection, channel, data)
                    else:
                        set_status(connection, channel, data.get("id"), data.get("topic"), data.get("status"), data.get("version"))
                        result = {"saved": True}
                self.send_json(HTTPStatus.OK, result)
            except (ValueError, UnicodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except (OSError, sqlite3.Error):
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Действие не выполнено. Повторите попытку."})

    if not 1024 <= port <= 65535:
        raise ValueError("Укажите порт от 1024 до 65535")
    with ThreadingHTTPServer(("127.0.0.1", port), EditorHandler) as server:
        print(f"Редактор вакансий: http://127.0.0.1:{port}", flush=True)
        server.serve_forever()
