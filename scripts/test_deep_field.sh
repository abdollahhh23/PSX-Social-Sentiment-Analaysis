#!/usr/bin/env bash
# Deep Field E2E — boots laya + worker, verifies the v3 API contract end to end.
# ONE invocation (sandbox reaps background processes between calls).
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOGS=/tmp/psx_deepfield_logs
mkdir -p "$LOGS"; rm -f "$LOGS"/*.log
PASS=0; FAIL=0
ck(){ if [ "$1" = "0" ]; then echo "  ✓ $2"; PASS=$((PASS+1)); else echo "  ✗ $2"; FAIL=$((FAIL+1)); fi }

echo "── 0. compile all"
python3 -m compileall -q config.py app.py run_worker.py data models engine daemon api viz scraper 2>"$LOGS/compile.log"
ck $? "compileall clean"

echo "── 1. boot laya engine :8000"
(cd "$ROOT/laya" && python3 app.py >"$LOGS/laya.log" 2>&1 & echo $! > "$LOGS/laya.pid")
sleep 1.2
curl -s --max-time 3 http://localhost:8000/health | grep -q '"status"' ; ck $? "laya /health"

echo "── 2. boot radar worker --demo :8001"
(cd "$ROOT" && python3 run_worker.py --demo >"$LOGS/worker.log" 2>&1 & echo $! > "$LOGS/worker.pid")
echo "   warming 26s (demo chatter + history + first refits)…"; sleep 26

echo "── 3. /api/feed deep-field contract"
curl -s --max-time 5 "http://localhost:8001/api/feed?asset=KSE-100" > "$LOGS/feed.json"
python3 - "$LOGS/feed.json" <<'PY'
import json, sys, math
d = json.load(open(sys.argv[1]))
def num(x): return isinstance(x,(int,float)) and math.isfinite(x)
checks = []
checks.append(("market block", isinstance(d.get("market"), dict) and num((d.get("market") or {}).get("price"))))
checks.append(("market.source sim/psx/yahoo", (d["market"].get("source") in ("sim","psx","yahoo"))))
checks.append(("models block", isinstance(d.get("models"), dict)))
m = d.get("models") or {}
checks.append(("vol metrics", num((m.get("vol") or {}).get("cc")) and m["vol"]["cc"] > 0))
checks.append(("pe present", m.get("pe") is not None))
ens = m.get("ensemble") or {}
checks.append(("ensemble fitted", bool(ens.get("fitted", ens.get("weights")))))
checks.append(("ensemble weights", isinstance(ens.get("weights"), dict) and len(ens.get("weights", {})) >= 3))
checks.append(("horizons fan", all(k in (ens.get("horizons") or {}) for k in ("1","3","5","10"))))
h5 = (ens.get("horizons") or {}).get("5", {})
checks.append(("fan has p10/p50/p90", all(h5.get(k) is not None for k in ("p10","p50","p90"))))
checks.append(("trail series", isinstance(m.get("trail"), list) and len(m["trail"]) >= 30))
arma = m.get("arma") or {}
checks.append(("arma fitted", "phi" in arma and "theta" in arma))
sym = m.get("symreg") or {}
checks.append(("symreg fitted+expr", bool(sym.get("expr"))))
hk = m.get("hawkes") or {}
checks.append(("hawkes fitted", hk.get("lambda_now") is not None and hk.get("branching") is not None))
cf = m.get("conformal") or {}
checks.append(("conformal calibrated", cf.get("fitted") is True and cf.get("q_hat", 0) > 0))
checks.append(("fci finite", num(d.get("fci"))))
fu = d.get("fusion") or {}
checks.append(("fusion breakdown", all(k in fu for k in ("cs","m_sig","align","cascade_pen","vol_damp","branching"))))
checks.append(("market_status", isinstance(d.get("market_status"), dict)))
bad = [n for n,ok in checks if not ok]
for n,ok in checks: print(("  ✓ " if ok else "  ✗ ") + n)
print("FCI=%s cs=%s m_sig=%s align=%s src=%s px=%s backend=%s expr=%s" % (
    d.get("fci"), fu.get("cs"), fu.get("m_sig"), fu.get("align"),
    (d.get("market") or {}).get("source"), (d.get("market") or {}).get("price"),
    (m.get("ensemble") or {}).get("backend"), (sym.get("expr") or "")[:40]))
sys.exit(1 if bad else 0)
PY
ck $? "feed deep-field contract"

echo "── 4. hot-add a company asset + verify within 30s"
curl -s --max-time 3 -X POST http://localhost:8001/api/watch -H 'Content-Type: application/json' -d '{"asset":"OGDC"}' >/dev/null
sleep 24
curl -s --max-time 5 "http://localhost:8001/api/feed?asset=OGDC" > "$LOGS/feed_ogdc.json"
python3 -c "
import json,sys
d=json.load(open('$LOGS/feed_ogdc.json'))
m=d.get('models') or {}
ok = (d.get('market') or {}).get('price') is not None and bool(m.get('ensemble'))
print('  ✓ OGDC scored+modeled' if ok else '  ✗ OGDC not modeled in time')
sys.exit(0 if ok else 1)"
ck $? "hot-add OGDC → market + models"

echo "── 5. surfaces serve"
for r in surface surface3d graph3d; do
  code=$(curl -s -o /tmp/sf.html -w "%{http_code}" "http://localhost:8001/$r?asset=KSE-100&nonce=1")
  [ "$code" = "200" ]; ck $? "$r → 200 ($(wc -c </tmp/sf.html) bytes)"
done

echo "── 6. worker log sanity (no tracebacks)"
if grep -q "Traceback" "$LOGS/worker.log"; then FAIL=$((FAIL+1)); echo "  ✗ tracebacks in worker.log"; grep -A3 Traceback "$LOGS/worker.log" | head -20; else echo "  ✓ no tracebacks"; PASS=$((PASS+1)); fi
grep -m2 "deep-field" "$LOGS/worker.log" | sed 's/^/  · /'
tail -2 "$LOGS/worker.log" | sed 's/^/  · /'

echo "── 7. teardown"
kill "$(cat "$LOGS/laya.pid")" 2>/dev/null; kill "$(cat "$LOGS/worker.pid")" 2>/dev/null; pkill -f run_worker.py 2>/dev/null; pkill -f "laya/app.py" 2>/dev/null
sleep 0.5
echo "═══ RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
