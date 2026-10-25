#!/usr/bin/env bash
# Visual QA v3.4 — pipeline hero + label pass.
# Boots laya + worker(--demo) + streamlit in ONE invocation (background
# processes die between bash tool calls — sandbox lesson, Task 11).
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOGS=/tmp/psx_v34_logs
mkdir -p "$LOGS"; rm -f "$LOGS"/*.log
OUT=/home/z/my-project/download

# clean runtime artifacts (packaging lesson, Task 12)
rm -f "$ROOT/daemon/psx_radar.db" "$ROOT/daemon/.autoboot" "$ROOT/daemon/autoboot.log" \
      "$ROOT/daemon/psx_market_cache.json" 2>/dev/null
find "$ROOT" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

(cd "$ROOT/laya" && python3 app.py >"$LOGS/laya.log" 2>&1 & echo $! > "$LOGS/laya.pid")
(cd "$ROOT" && python3 run_worker.py --demo >"$LOGS/worker.log" 2>&1 & echo $! > "$LOGS/worker.pid")
echo "[qa] worker warming 24s…"; sleep 24

echo "[qa] health:"; curl -s "http://localhost:8001/api/health" | python3 -c "
import json,sys
h=json.load(sys.stdin)
print('  tick',h.get('tick'),'| scored',h.get('posts_scored'),'| ingested',h.get('posts_ingested'),
      '| dropped',h.get('dropped'),'| backfill',{k:h.get('backfill',{}).get(k) for k in ('mode','depth_days','chunks')})"
echo "[qa] feed:"; curl -s "http://localhost:8001/api/feed?asset=KSE-100" | python3 -c "
import json,sys
f=json.load(sys.stdin)
print('  sources',f.get('sources'),'| per_min',f.get('ingest_per_min'),
      '| split',f.get('bull_n'),f.get('neutral_n'),f.get('bear_n'))"

(cd "$ROOT" && python3 -m streamlit run app.py --server.headless true --server.port 8501 >"$LOGS/app.log" 2>&1 & echo $! > "$LOGS/app.pid")
sleep 10

shot(){
  agent-browser open "$1" >/dev/null 2>&1
  sleep "$3"
  agent-browser screenshot "$2" >/dev/null 2>&1 && echo "[qa] shot $2" || echo "[qa] FAIL $2"
}
shot "http://localhost:8501" "$OUT/psx_v34_hero.png" 9
shot "http://localhost:8001/surface3d?asset=KSE-100&nonce=7" "$OUT/psx_v34_deck.png" 6
echo "[qa] browser errors:"; agent-browser errors 2>/dev/null | head -5
echo "[qa] app.log tail:"; tail -3 "$LOGS/app.log"

kill "$(cat "$LOGS/laya.pid")" "$(cat "$LOGS/worker.pid")" "$(cat "$LOGS/app.pid")" 2>/dev/null
pkill -f run_worker.py 2>/dev/null; pkill -f "laya/app.py" 2>/dev/null; pkill -f "streamlit run" 2>/dev/null
echo "[qa] done"
