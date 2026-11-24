"""Automated Prescription Safety Auditing and Adverse Drug Interaction Advisor - FastAPI app."""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import httpx

from . import access, ai_groq, clinical, config, db, pipeline
from .errors import ApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("prescription-safety")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    if config.DEMO_MODE and not config.DEMO_TICKET_SECRET:
        log.warning("Hosted mode is on but no access secret is set: every live run will be refused; samples still work.")
    yield


app = FastAPI(title=config.PROJECT_NAME, version=config.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def err(status, code, message):
    return JSONResponse({"error": message, "code": code}, status_code=status)


@app.exception_handler(ApiError)
async def _api_error(_r, exc):
    return err(exc.status, exc.code, exc.message)


@app.exception_handler(ai_groq.AIError)
async def _ai_error(_r, exc):
    return err(exc.status, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_r, exc):
    if exc.status_code == 404:
        return err(404, "NOT_FOUND", "That page or item doesn't exist.")
    if exc.status_code == 405:
        return err(405, "BAD_INPUT", "That method isn't allowed here.")
    return err(exc.status_code, "BAD_INPUT", "The request couldn't be processed.")


@app.exception_handler(RequestValidationError)
async def _validation_error(_r, _exc):
    return err(400, "BAD_INPUT", "Some of the fields in that request are invalid.")


@app.exception_handler(Exception)
async def _unhandled(_r, exc):
    log.exception("Unhandled error: %s", type(exc).__name__)
    return err(500, "AI_FAILED", "Something went wrong on the server. Please try again.")


@app.middleware("http")
async def _size_cap(request: Request, call_next):
    """Reject oversized request bodies (MAX_UPLOAD_MB, or DEMO_MAX_UPLOAD_MB in demo mode) with 413 TOO_LARGE."""
    limit_mb = config.DEMO_MAX_UPLOAD_MB if config.DEMO_MODE else config.MAX_UPLOAD_MB
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        size = 0
    if size > limit_mb * 1024 * 1024:
        return err(413, "TOO_LARGE", f"That request is larger than the {limit_mb} MB limit. Send a shorter medicine list.")
    return await call_next(request)


# ---------------------------------------------------------------- standard routes
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def api_info(request: Request):
    runs_left, demo = access.info(request)
    key, _ = ai_groq.resolve_key()
    return {
        "name": config.PROJECT_NAME, "slug": config.PROJECT_SLUG, "number": config.PROJECT_NUMBER, "version": config.VERSION,
        "mode": "demo" if config.DEMO_MODE else "local",
        "ai": {"provider": "groq", "ready": key is not None, "model": ai_groq.last_model or config.GROQ_MODEL},
        "limits": {"max_upload_mb": config.DEMO_MAX_UPLOAD_MB if config.DEMO_MODE else config.MAX_UPLOAD_MB,
                   "hourly_runs": config.HOURLY_RUNS, "runs_left": runs_left},
        "demo": demo,
        "context_options": [{"id": k, "label": v[0]} for k, v in pipeline.CONTEXT_FLAGS.items()],
        "min_medicines": clinical.MIN_DRUGS, "max_medicines": clinical.MAX_DRUGS, "disclaimer": pipeline.DISCLAIMER,
    }


def _samples():
    items = []
    for path in sorted(config.SAMPLES_DIR.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        item["has_saved_result"] = (config.SAMPLES_DIR / "results" / f"{item['id']}.json").is_file()
        items.append(item)
    items.sort(key=lambda s: (s.get("order", 99), s["id"]))
    return items


@app.get("/api/samples")
async def api_samples():
    return {"samples": _samples()}


@app.get("/api/samples/{sample_id}")
async def api_sample(sample_id: str):
    item = next((s for s in _samples() if s["id"] == sample_id), None)
    if not item:
        raise ApiError(404, "NOT_FOUND", "That sample doesn't exist.")
    result = None
    if item["has_saved_result"]:
        result = json.loads((config.SAMPLES_DIR / "results" / f"{sample_id}.json").read_text(encoding="utf-8"))
    return {"id": item["id"], "title": item["title"], "input_text": item["input_text"], "result": result, "context": item.get("context")}


# ---------------------------------------------------------------- API keys (download version only)
def _keys_guard(request: Request):
    if config.DEMO_MODE:
        raise ApiError(404, "NOT_FOUND", "That page or item doesn't exist.")
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1", "localhost") or "x-forwarded-for" in request.headers or "forwarded" in request.headers:
        raise ApiError(403, "KEYS_LOCAL_ONLY", "API keys can only be changed from the computer that runs this app.")


@app.get("/api/keys")
async def keys_get(request: Request):
    if config.DEMO_MODE:
        raise ApiError(404, "NOT_FOUND", "That page or item doesn't exist.")
    key, source = ai_groq.resolve_key()
    return {"groq": {"configured": key is not None, "source": source, "masked": ai_groq.mask(key)}}


@app.post("/api/keys")
async def keys_set(request: Request):
    _keys_guard(request)
    try:
        body = await request.json()
        value = body.get("groq_api_key") if isinstance(body, dict) else None
    except Exception:
        value = None
    if not isinstance(value, str) or not ai_groq.usable_key(value.strip()):
        raise ApiError(400, "BAD_INPUT", "That doesn't look like a Groq key. It should start with gsk_ and have no spaces.")
    db.kv_set("groq_api_key", value.strip())
    return {"saved": ["groq"]}


@app.delete("/api/keys/groq")
async def keys_delete(request: Request):
    _keys_guard(request)
    db.kv_delete("groq_api_key")
    return {"removed": "groq"}


# ---------------------------------------------------------------- history
@app.get("/api/history")
async def history_list(request: Request):
    cid = access.client_id(request)
    return {"items": db.history_list(cid) if cid else []}


@app.get("/api/history/{item_id}")
async def history_item(item_id: int, request: Request):
    cid = access.client_id(request)
    item = db.history_get(cid, item_id) if cid else None
    if not item:
        raise ApiError(404, "NOT_FOUND", "That history entry doesn't exist.")
    return item


@app.delete("/api/history")
async def history_clear(request: Request):
    cid = access.client_id(request)
    if cid:
        db.history_clear(cid)
    return {"cleared": True}


# ---------------------------------------------------------------- feature routes
async def _body(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "BAD_INPUT", "Send a JSON body with a list of medicines.")
    if not isinstance(body, dict):
        raise ApiError(400, "BAD_INPUT", "Send a JSON object with a list of medicines.")
    return body


def _items(body):
    return clinical.split_medications(body.get("medications"), body.get("text"))


def _title(result):
    meds = result["input"]["medications"]
    return " + ".join(m.title() for m in meds[:4]) + (f" +{len(meds) - 4}" if len(meds) > 4 else "")


@app.get("/api/drugs/lookup")
async def drug_lookup(q: str = ""):
    """RxNorm normalisation for the medicine chips. Free: no quota, no Groq."""
    q = q.strip()[:80]
    if len(clinical.clean_name(q)) < 3:
        return {"query": q, "match": None}
    async with httpx.AsyncClient(timeout=clinical.TIMEOUT_S, headers={"Accept": "application/json"}) as client:
        r = await clinical.resolve_rxnorm(client, q)
    if not r:
        return {"query": q, "match": None}
    return {"query": q, "match": {"rxcui": r["rxcui"], "name": r["rxnorm_name"], "generic": r["ingredients"][0]["name"], "tty": r["tty"]}}


@app.get("/api/drugs/suggest")
async def drug_suggest(q: str = ""):
    """Type-ahead names from RxNorm (approximate match). Free: no quota, no Groq."""
    q = clinical.clean_name(q.strip()[:80])
    if len(q) < 3:
        return {"query": q, "suggestions": []}
    url = f"{config.RXNORM_API_BASE}/approximateTerm.json"
    async with httpx.AsyncClient(timeout=clinical.TIMEOUT_S, headers={"Accept": "application/json"}) as client:
        try:
            _, body = await clinical._get(client, url, {"term": q, "maxEntries": 12}, ttl=86400, ok_missing=True)
        except ApiError:
            body = {}
        names, seen = [], set()
        for c in ((body.get("approximateGroup") or {}).get("candidate")) or []:
            rx = c.get("rxcui")
            if not rx or rx in seen:
                continue
            seen.add(rx)
            _, pb = await clinical._get(client, f"{config.RXNORM_API_BASE}/rxcui/{rx}/properties.json", ttl=clinical.TTL_LABEL, ok_missing=True)
            p = pb.get("properties") or {}
            if p.get("tty") in ("IN", "BN", "PIN", "MIN") and p.get("name"):
                names.append({"name": p["name"], "rxcui": rx, "tty": p["tty"]})
            if len(names) >= 6:
                break
    return {"query": q, "suggestions": names}


async def _feature(request: Request, kind):
    body = await _body(request)
    slot = access.reserve(request)  # takes one of the 15 hourly runs; refunded below if anything fails
    cid = access.client_id(request)
    try:
        items = _items(body)
        if kind == "alternative":
            payload = await pipeline.run_alternative(items, body.get("replace"), body.get("alternative"), body.get("context"))
            title = f'{payload["replace"].title()} → {payload["alternative"].title()}'
        else:
            payload = await pipeline.run_audit(items, body.get("context"))
            title = _title(payload)
    except BaseException as exc:
        access.release(slot)
        log.info("Run refunded for %s (%s)", access.client_ip(request), getattr(exc, "code", type(exc).__name__))
        raise
    try:
        db.history_add(cid, kind, title, payload)
    except Exception:
        log.warning("Couldn't save history")
    return payload


@app.post("/api/audit")
async def api_audit(request: Request):
    return await _feature(request, "audit")


@app.post("/api/check-alternative")
async def api_alternative(request: Request):
    return await _feature(request, "alternative")


# ---------------------------------------------------------------- frontend
@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
