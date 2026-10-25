#!/usr/bin/env bash
# Task 10 — "heartbeat" patch E2E:
#  1. compileall on the touched modules
#  2. boot the worker LIVE (real sources; sandbox: reddit 403 expected)
#  3. assert /api/feed ships the new liveness fields and tick advances
#  4. assert /surface HTML contains the new HUD blocks; node --check the JS
set -u
cd "$(dirname "$0")/.."
PROJ="$(pwd)"
FAIL=0
ok(){ echo "  [PASS] $1"; }
bad(){ echo "  [FAIL] $1"; FAIL=1; }

echo "── 1. compile"
python3 -m compileall -q config.py daemon/store.py daemon/worker.py \
    scraper/social_feed.py viz/surface.py api/server.py \
    && ok "compileall clean" || bad "compileall"

echo "── 2. boot worker (live)"
rm -f daemon/psx_radar.db            # cold start for a deterministic test
python3 run_worker.py > /tmp/hb_worker.log 2>&1 &
WPID=$!
READY=0
for i in $(seq 1 30); do             # readiness poll — cold imports can be slow
  sleep 2
  if curl -s --max-time 3 'http://localhost:8001/api/health' | grep -q '"ok":true'; then
    READY=1; echo "  worker API up after ~$((i*2))s"; break
  fi
done
[ "$READY" -eq 1 ] && ok "worker booted" || { bad "worker never came up"; tail -20 /tmp/hb_worker.log; }

echo "── 3. /api/feed liveness fields"
FEED="$(curl -s --max-time 5 'http://localhost:8001/api/feed?asset=KSE-100')"
printf '%s' "$FEED" > /tmp/hb_feed.json
for k in last_new_age_s ingest_per_min tick_ms posts_scored posts_ingested; do
  python3 -c "import json;d=json.load(open('/tmp/hb_feed.json'));d['$k']" 2>/dev/null \
      && ok "field $k present" || bad "field $k missing"
done
T1="$(echo "$FEED" | python3 -c "import sys,json;print(json.load(sys.stdin)['tick'])" 2>/dev/null)"
sleep 3
T2="$(curl -s --max-time 5 'http://localhost:8001/api/feed?asset=KSE-100' \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['tick'])" 2>/dev/null)"
[ -n "$T1" ] && [ -n "$T2" ] && [ "$T2" -gt "$T1" ] \
    && ok "tick advancing ($T1 → $T2)" || bad "tick not advancing ($T1 → $T2)"
echo "$FEED" | python3 -c "import sys,json;d=json.load(sys.stdin);a=d['last_new_age_s'];assert a is None or (0 <= a <= d['uptime_s'] + 5), a" \
    && ok "last_new_age_s semantics sane" || bad "last_new_age_s semantics"

echo "── 4. /surface HUD blocks"
SURF="$(curl -s --max-time 5 'http://localhost:8001/surface?asset=KSE-100')"
for pat in 'id="ln"' 'id="ir"' 'id="quietChip"' 'Ingest Pulse' 'Last New Signal' 'last_new_age_s' 'ingest_per_min' 'bing_news' 'wires'; do
  echo "$SURF" | grep -q "$pat" && ok "surface has $pat" || bad "surface missing $pat"
done

echo "── 5. JS syntax"
printf '%s' "$SURF" > /tmp/hb_surf.html
python3 -c "
import re
html = open('/tmp/hb_surf.html').read()
m = re.search(r'<script type=\"module\">(.*?)</script>', html, re.S)
assert m, 'module script not found'
open('/tmp/hb_surface.js', 'w').write(m.group(1))
" && ok "JS extracted" || bad "JS extraction"
node --check /tmp/hb_surface.js && ok "surface JS parses" || bad "surface JS syntax"

echo "── worker log tail"
tail -n 6 /tmp/hb_worker.log
kill "$WPID" 2>/dev/null; wait "$WPID" 2>/dev/null
rm -f daemon/psx_radar.db
echo "════════════════════════════════════"
if [ "$READY" -ne 1 ]; then echo "HEARTBEAT E2E — FAILURES PRESENT (no API)"; exit 1; fi
[ "$FAIL" -eq 0 ] && echo "HEARTBEAT E2E — ALL PASS" || echo "HEARTBEAT E2E — FAILURES PRESENT"
exit "$FAIL"
