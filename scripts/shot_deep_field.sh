#!/usr/bin/env bash
# Visual QA — boot laya + worker + streamlit, screenshot all three surfaces.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOGS=/tmp/psx_shot_logs
mkdir -p "$LOGS"; rm -f "$LOGS"/*.log /home/z/my-project/download/psx_df_*.png
OUT=/home/z/my-project/download

(cd "$ROOT/laya" && python3 app.py >"$LOGS/laya.log" 2>&1 & echo $! > "$LOGS/laya.pid")
(cd "$ROOT" && python3 run_worker.py --demo >"$LOGS/worker.log" 2>&1 & echo $! > "$LOGS/worker.pid")
echo "[qa] worker warming 24s…"; sleep 24
(cd "$ROOT" && python3 -m streamlit run app.py --server.headless true --server.port 8501 >"$LOGS/app.log" 2>&1 & echo $! > "$LOGS/app.pid")
sleep 9

shot(){
  agent-browser open "$1" >/dev/null 2>&1
  sleep "$3"
  agent-browser screenshot "$2" >/dev/null 2>&1 && echo "[qa] shot $2" || echo "[qa] FAIL $2"
}
shot "http://localhost:8001/surface3d?asset=KSE-100&nonce=7" "$OUT/psx_df_forecast.png" 6
shot "http://localhost:8001/graph3d?asset=KSE-100&nonce=7"   "$OUT/psx_df_graph.png" 6
shot "http://localhost:8501"                                  "$OUT/psx_df_app.png" 8
agent-browser errors 2>/dev/null | head -5
kill "$(cat "$LOGS/laya.pid")" "$(cat "$LOGS/worker.pid")" "$(cat "$LOGS/app.pid")" 2>/dev/null
pkill -f run_worker.py 2>/dev/null; pkill -f "laya/app.py" 2>/dev/null; pkill -f "streamlit run" 2>/dev/null
echo "[qa] done"
