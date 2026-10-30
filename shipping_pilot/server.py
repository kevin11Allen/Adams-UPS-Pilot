"""Run: UPS_CONFIG_PATH=/private/path/config.json python3 server.py --port 8765."""
import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

try:
    from .ups_client import UPSClient, PilotError, validate
except ImportError:
    from ups_client import UPSClient, PilotError, validate


class LabelCache:
    """Process-lifetime duplicate guard; uncertain attempts stay blocked.

    A restart loses this state. Never change a key/restart to retry an uncertain
    shipment; reconcile that attempt first. No automatic shipment retries.
    """
    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()

    def create(self, client, body):
        if body.get("confirm") is not True:
            raise PilotError("confirmation_required", "Explicit CIE label confirmation is required.")
        key = body.get("idempotency_key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", key):
            raise PilotError("invalid_input", "Provide a new idempotency key of 16–100 letters, digits, hyphens or underscores.")
        data = validate(body)
        if not data["origin"]["phone"]:
            raise PilotError("invalid_input", "Origin phone is required to request a CIE label.")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.lock:
            previous = self.entries.get(key)
            if previous:
                if previous["digest"] != digest:
                    raise PilotError("idempotency_conflict", "This key was already used with different shipment data.", 409)
                if previous.get("result"):
                    return previous["result"]
                raise PilotError("shipment_unconfirmed", "This shipment is pending or its result is unconfirmed. Do not retry using a new key.", 409)
            if len(self.entries) >= 100:
                raise PilotError("session_limit", "Pilot session limit reached. Reconcile all attempts before restarting.", 409)
            self.entries[key] = {"digest": digest}
        # Any failure is conservatively retained, including network timeouts.
        result = client.label(data)
        with self.lock:
            self.entries[key]["result"] = result
        return result


class PilotServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, client=None):
        self.client = client if client is not None else UPSClient()
        self.label_cache = LabelCache()
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalCIEPilot"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, status, payload, content_type="application/json"):
        raw = json.dumps(payload).encode() if content_type == "application/json" else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def guard(self, post=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Host") != [expected]:
            raise PilotError("forbidden", "Open this pilot using its exact 127.0.0.1 URL.", 403)
        if post and self.headers.get_all("Origin") != ["http://" + expected]:
            raise PilotError("forbidden", "Same-origin browser requests are required.", 403)

    def error(self, error):
        self.reply(error.status, {"error": {"code": error.code, "message": error.message}, "environment": "CIE"})

    def do_GET(self):
        try:
            self.guard()
            route = urlsplit(self.path)
            if self.path == "/api/status":
                self.reply(200, self.server.client.status())
            elif route.path == "/" and not route.scheme and not route.netloc and not route.fragment:
                self.reply(200, Path(__file__).with_name("index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/app.js":
                self.reply(200, Path(__file__).with_name("app.js").read_bytes(), "text/javascript; charset=utf-8")
            else:
                self.reply(404, {"error": {"code": "not_found", "message": "Not found."}})
        except PilotError as exc:
            self.error(exc)
        except Exception:
            self.error(PilotError("internal_error", "Local pilot could not complete this request.", 500))

    def do_POST(self):
        try:
            self.guard(post=True)
            if self.path not in ("/api/test-connection", "/api/rates", "/api/label"):
                raise PilotError("not_found", "Not found.", 404)
            if self.headers.get("Transfer-Encoding") is not None:
                raise PilotError("invalid_request", "Transfer encoding is unsupported.")
            if len(self.headers.get_all("Content-Type", [])) != 1 or self.headers.get_content_type() != "application/json":
                raise PilotError("invalid_request", "application/json is required.", 415)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"\d{1,8}", lengths[0]):
                raise PilotError("invalid_request", "A valid Content-Length is required.", 411)
            size = int(lengths[0])
            if not 0 < size <= 65536:
                raise PilotError("request_too_large", "Request must be between 1 and 65536 bytes.", 413)
            self.connection.settimeout(10)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise PilotError("invalid_request", "Incomplete JSON body.")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise PilotError("invalid_input", "Invalid JSON body.") from None
            if not isinstance(body, dict):
                raise PilotError("invalid_input", "JSON object required.")
            if self.path == "/api/test-connection":
                result = self.server.client.test_connection()
            elif self.path == "/api/rates":
                result = self.server.client.rates(validate(body))
            else:
                result = self.server.label_cache.create(self.server.client, body)
            self.reply(200, result)
        except PilotError as exc:
            self.error(exc)
        except Exception:
            self.error(PilotError("internal_error", "Local pilot could not complete this request. Do not retry an unconfirmed shipment with a new key.", 500))


def main():
    parser = argparse.ArgumentParser(description="Local UPS CIE pilot (no production support)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    server = PilotServer(args.port)
    print(f"UPS CIE pilot: http://127.0.0.1:{server.server_port} — live shipping disabled", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
