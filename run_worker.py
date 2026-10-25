"""
Entry point — starts the always-on radar worker AND the feed API server in
ONE process:

    python run_worker.py            # live mode (Laya on :8000 optional)
    python run_worker.py --demo     # + synthetic PSX chatter (offline testing)
    python run_worker.py --port 8001 --host 0.0.0.0

Then, in a second terminal:

    streamlit run app.py            # dashboard on :8501
"""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

import config as C
from api.server import app, register_worker
from daemon.worker import RadarWorker

BANNER = r"""
  ┌─────────────────────────────────────────────────────────┐
  │   ◆ PSX RADAR — INSTITUTIONAL SENTIMENT PIPELINE        │
  │   1s tick · ALS-gated · Laya System 1 · WCI decay       │
  └─────────────────────────────────────────────────────────┘
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description="PSX Radar always-on pipeline")
    parser.add_argument("--demo", action="store_true",
                        help="inject synthetic PSX chatter (offline testing)")
    parser.add_argument("--host", default=C.API_HOST)
    parser.add_argument("--port", type=int, default=C.API_PORT)
    args = parser.parse_args()

    print(BANNER)
    worker = RadarWorker(demo=args.demo)
    register_worker(worker)

    server_cfg = uvicorn.Config(app, host=args.host, port=args.port,
                                log_level="warning", loop="asyncio")
    server = uvicorn.Server(server_cfg)

    print(f"[radar] feed API  → http://localhost:{args.port}/api/feed")
    print(f"[radar] laya      → {C.LAYA_ENDPOINT}")
    print(f"[radar] tick      → {C.TICK_INTERVAL_S}s · budget {C.INFERENCE_BUDGET_PER_TICK} inferences")
    print("[radar] CTRL+C to stop\n")
    try:
        await asyncio.gather(server.serve(), worker.run())
    except asyncio.CancelledError:
        pass
    finally:
        worker_state_flush = getattr(__import__("daemon.store", fromlist=["STATE"]).STATE,
                                     "persist_flush")
        worker_state_flush()
        print("\n[radar] shutdown clean — state persisted")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
