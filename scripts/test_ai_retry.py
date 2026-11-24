"""Offline tests for app/ai_groq.py: retries, backoff, model fallback, error mapping. No network, no real key.

    .venv/Scripts/python scripts/test_ai_retry.py
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai_groq, config  # noqa: E402

GOOD = {"choices": [{"message": {"content": "<think>hmm</think>```json\n{\"ok\": true}\n```"}}]}
validate = lambda d: d if d.get("ok") else (_ for _ in ()).throw(ValueError("bad"))
failures = []


def run(name, handler, expect_model=None, expect_code=None, expect_sleeps=None, expect_calls=None):
    calls, sleeps = [], []

    async def fake_sleep(s):
        sleeps.append(round(s, 1))

    def transport(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        return handler(len(calls), body["model"])

    real_client = httpx.AsyncClient
    ai_groq.httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(transport), **{k: v for k, v in kw.items() if k != "transport"})
    ai_groq.asyncio.sleep = fake_sleep
    ai_groq.resolve_key = lambda: ("gsk_" + "a" * 30, "env")
    try:
        try:
            _, model = asyncio.run(ai_groq.chat_json("s", "u", validate))
            got = ("ok", model, None)
        except ai_groq.AIError as e:
            got = ("err", None, e.code)
    finally:
        ai_groq.httpx.AsyncClient = real_client
    ok = True
    if expect_model and got[1] != expect_model:
        ok = False
    if expect_code and got[2] != expect_code:
        ok = False
    if expect_calls is not None and len(calls) != expect_calls:
        ok = False
    if expect_sleeps is not None and [int(x) for x in sleeps] != expect_sleeps:
        ok = False
    print(("PASS " if ok else "FAIL ") + name + f"  -> {got}, calls={calls}, sleeps={sleeps}")
    if not ok:
        failures.append(name)


P, F = config.GROQ_MODEL, config.GROQ_FALLBACK_MODEL
resp = lambda status, text="", js=None: httpx.Response(status, json=js) if js is not None else httpx.Response(status, text=text)

run("200 first try", lambda n, m: resp(200, js=GOOD), expect_model=P, expect_calls=1)
run("503 twice then ok (same model, 1s+2s backoff)", lambda n, m: resp(503, "busy") if n < 3 else resp(200, js=GOOD), expect_model=P, expect_calls=3, expect_sleeps=[1, 2])
run("primary 503 x3 -> fallback model answers", lambda n, m: resp(503, "busy") if m == P else resp(200, js=GOOD), expect_model=F, expect_calls=4)
run("429 'try again in 2s' honoured", lambda n, m: resp(429, "Please try again in 2s") if n == 1 else resp(200, js=GOOD), expect_model=P, expect_sleeps=[2])
run("429 'try again in 40s' (>20) uses exponential backoff", lambda n, m: resp(429, "Please try again in 40s") if n == 1 else resp(200, js=GOOD), expect_model=P, expect_sleeps=[1])
run("all 429/503 on both models -> AI_BUSY", lambda n, m: resp(429, "rate"), expect_code="AI_BUSY", expect_calls=6)
run("500, 502, 504 are all retried (3 attempts), then the fallback model answers", lambda n, m: resp([500, 502, 504][n - 1], "x") if n <= 3 else resp(200, js=GOOD), expect_model=F, expect_calls=4)
run("401 -> AI_KEY_INVALID, no retry", lambda n, m: resp(401, "invalid api key"), expect_code="AI_KEY_INVALID", expect_calls=1)


def timeouts(n, m):
    raise httpx.ReadTimeout("slow")


run("timeouts everywhere -> AI_BUSY", timeouts, expect_code="AI_BUSY", expect_calls=6)
run("400 on primary -> fallback tried, then AI_FAILED", lambda n, m: resp(400, "bad request"), expect_code="AI_FAILED", expect_calls=2)
bad_then_good = lambda n, m: resp(200, js={"choices": [{"message": {"content": "not json"}}]}) if n == 1 else resp(200, js=GOOD)
run("invalid JSON once -> retried with a reminder, then ok", bad_then_good, expect_model=P, expect_calls=2)

sys.exit(1 if failures else 0)
