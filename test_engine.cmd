@echo off
cd /d "%~dp0"
echo === LAYA HEALTH (GET /health) ===
curl -s http://localhost:8000/health
echo.
echo.
echo === LAYA SCORE (POST /v1/systemone with laya\payload.json) ===
curl -s -X POST http://localhost:8000/v1/systemone -H "Content-Type: application/json" -d @laya\payload.json
echo.
echo.
echo If SCORE shows "answers" with directional_bias / institutional_relevance
echo / urgency_score, the engine is wired correctly to the radar.
