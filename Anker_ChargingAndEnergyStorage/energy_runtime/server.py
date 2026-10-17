"""Authenticated loopback tool API; never accepts entity IDs, URLs or watts."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urlparse

from .live import LocalHA
from .release import ReleasePipeline
from .runtime import ENVIRONMENT, SCHEMA, Rejected, Runtime, canonical

PRIVATE = Path(os.environ["LOCALAPPDATA"]) / "AnkerEnergyRuntime"
PORT = 19389


def serve():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    token_file = PRIVATE / "api.json"
    if token_file.exists():
        token = json.loads(token_file.read_text(encoding="utf-8"))["token"]
    else:
        token = secrets.token_hex(32)
        token_file.write_text(canonical({"url": f"http://127.0.0.1:{PORT}", "token": token}), encoding="utf-8")
    runtime = Runtime(LocalHA(), PRIVATE / "episodes.sqlite3")
    release_pipeline = ReleasePipeline()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, payload):
            data = canonical(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if self.headers.get("Origin") or self.headers.get("Host") != f"127.0.0.1:{PORT}":
                return False
            return hmac.compare_digest(self.headers.get("X-Energy-Runtime-Key", ""), token)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                try:
                    runtime.adapter.guard()
                    result = {"status": "ready"}
                except Exception as error:
                    result = {"status": "blocked", "reason": str(error)}
                return self.reply(200, {"schemaVersion": SCHEMA, "environment": ENVIRONMENT, **result})
            if not self.authorized():
                return self.reply(403, {"error": "forbidden"})
            try:
                if parsed.path == "/environment/snapshot":
                    if parsed.query:
                        raise Rejected("environment_query_not_allowed")
                    return self.reply(200, release_pipeline.snapshot())
                if parsed.path == "/snapshot":
                    return self.reply(200, runtime.snapshot())
                if parsed.path == "/episode":
                    args = parse_qs(parsed.query)
                    return self.reply(200, runtime.episode(args["episodeId"][0], args["ownerKey"][0]))
                if parsed.path == "/decision":
                    args = parse_qs(parsed.query)
                    if set(args) not in ({"ownerKey", "episodeId"}, {"ownerKey", "previewId"}) or any(len(values) != 1 for values in args.values()):
                        raise Rejected("invalid_decision_reference")
                    return self.reply(200, runtime.decision(**{key: values[0] for key, values in args.items()}))
                return self.reply(404, {"error": "unknown_tool"})
            except (Rejected, KeyError) as error:
                return self.reply(409, {"error": str(error)})
            except Exception:
                return self.reply(500, {"error": "runtime_read_failed"})

        def do_POST(self):
            if not self.authorized():
                return self.reply(403, {"error": "forbidden"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.reply(415, {"error": "json_required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256_000:
                    raise Rejected("request_size_invalid")
                body = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite_json")))
                if self.path == "/proposal":
                    if set(body) not in ({"owner", "effectiveConstraints", "requestId"}, {"owner", "effectiveConstraints", "requestId", "previewId"}):
                        raise Rejected("invalid_proposal_fields")
                    return self.reply(200, runtime.propose(**body))
                if self.path == "/preview":
                    if set(body) not in ({"owner", "effectiveConstraints", "candidate", "requestId"}, {"owner", "effectiveConstraints", "candidate", "requestId", "parentEpisodeId"}):
                        raise Rejected("invalid_preview_fields")
                    return self.reply(200, runtime.preview(**body))
                if self.path == "/confirm":
                    if set(body) != {"owner", "episodeId", "proposalId", "confirmationId"}:
                        raise Rejected("invalid_confirmation_fields")
                    return self.reply(200, runtime.confirm(**body))
                if self.path == "/reconcile":
                    if set(body) != {"owner", "episodeId"}:
                        raise Rejected("invalid_reconcile_fields")
                    return self.reply(200, runtime.reconcile(**body))
                if self.path == "/stop":
                    if set(body) != {"owner", "requestId"}:
                        raise Rejected("invalid_stop_fields")
                    return self.reply(200, runtime.stop(**body))
                return self.reply(404, {"error": "unknown_tool"})
            except (Rejected, KeyError, TypeError, ValueError) as error:
                return self.reply(409, {"error": str(error)})
            except Exception:
                return self.reply(500, {"error": "runtime_tool_failed_reconcile_episode"})

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    status = {"pid": os.getpid(), "environment": ENVIRONMENT, "port": PORT,
              "sourceHashes": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(__file__).parent.glob("*.py")}}
    (PRIVATE / "status.json").write_text(canonical(status), encoding="utf-8")
    print(f"Energy runtime local tools: http://127.0.0.1:{PORT}; credentials in private user directory", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    serve()
