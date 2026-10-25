"""
Laya System 1 — HTTP surface (ZERO-DEPENDENCY, stdlib only).

Why stdlib? Docker builds must succeed on slow/unstable networks — with no
third-party packages there is no `pip install` step, so the image build never
touches PyPI at all.

Endpoints (identical contract to the original FastAPI surface):

  GET  /health          → liveness + mode + counters (radar probes this)
  POST /v1/systemone    → answer the 3 typed decision questions for one post
  GET  /                → service info

Runs standalone (python app.py) or in Docker (see Dockerfile).
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engine as core

_STARTED = time.time()
_STATS = {"scored": 0, "errors": 0}


class Handler(BaseHTTPRequestHandler):
    server_version = "LayaS1/" + core.VERSION
    protocol_version = "HTTP/1.1"          # keep-alive → pooled radar client stays fast

    # ------------------------------------------------------------ helpers
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:                                 # noqa: N802
        route = self._route()
        if route == "/health":
            self._send(200, {
                "status": "ok",
                "engine": "laya-system-1",
                "mode": core.active_mode(),
                "version": core.VERSION,
                "uptime_s": round(time.time() - _STARTED, 1),
                "scored": _STATS["scored"],
            })
        elif route == "/":
            self._send(200, {
                "service": "laya-system-1",
                "version": core.VERSION,
                "mode": core.active_mode(),
                "endpoints": {"health": "GET /health", "score": "POST /v1/systemone"},
            })
        else:
            self._send(404, {"error": f"no route {route}"})

    # --------------------------------------------------------------- POST
    def do_POST(self) -> None:                                # noqa: N802
        t0 = time.perf_counter()
        if self._route() != "/v1/systemone":
            self._send(404, {"error": "not found — POST /v1/systemone"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            _STATS["errors"] += 1
            self._send(400, {"error": "invalid json body"})
            return

        data = data if isinstance(data, dict) else {}
        inp = data.get("input") or {}
        state = data.get("state") or {}
        asset = state.get("asset") or {}
        author = state.get("author") or {}

        verdict = core.score(str(inp.get("text") or ""))
        _STATS["scored"] += 1

        self._send(200, {
            "engine": "system-1",
            "model": {"name": "laya-s1", "mode": core.active_mode(), "version": core.VERSION},
            "asset": asset.get("symbol"),
            "answers": {
                "directional_bias": {
                    "value": verdict["direction"],
                    "confidence": verdict["confidence"],
                },
                "institutional_relevance": {
                    "value": verdict["relevance"],
                    "confidence": verdict["relevance"],
                },
                "urgency_score": {
                    "value": verdict["urgency"],
                    "confidence": verdict["urgency_conf"],
                },
            },
            "rationale": {
                "matched_terms": verdict["matched"],
                "net_stance": verdict["net"],
            },
            "meta": {
                "author_als": author.get("als"),
                "source": author.get("source"),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
        })


if __name__ == "__main__":
    port = int(os.getenv("LAYA_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[laya] System 1 listening on 0.0.0.0:{port} — mode={core.active_mode()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[laya] shutdown clean")
