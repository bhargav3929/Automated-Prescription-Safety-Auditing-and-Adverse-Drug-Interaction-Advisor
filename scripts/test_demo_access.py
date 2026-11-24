"""Offline tests for the hosted-mode access rules (app/access.py, app/db.py). Uses a throw-away database and a stubbed
audit, so it needs no network and no Groq key.

    .venv/Scripts/python scripts/test_demo_access.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

SECRET = "test-secret-0123456789"
os.environ.update({"DEMO_MODE": "true", "DEMO_TICKET_SECRET": SECRET, "DEMO_DAILY_CAP": "2", "PROJECT_SLUG": "prescription-safety-advisor"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path.home() / "Desktop" / "lastsem-architecture" / "scripts"))

from app import config  # noqa: E402

config.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, main  # noqa: E402
from app.errors import ApiError  # noqa: E402
from mint_ticket import mint  # noqa: E402

fail_next = {"on": False}


async def fake_audit(items, ctx):
    if fail_next["on"]:
        raise ApiError(400, "BAD_INPUT", "Couldn't verify one or more medication names in RxNorm/openFDA: x")
    return {"kind": "audit", "input": {"medications": items, "context": ["Standard Adult"]}}


main.pipeline.run_audit = fake_audit
client = TestClient(main.app)
BODY = {"medications": ["warfarin", "aspirin"]}
failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


def post(ticket=None, body=BODY):
    return client.post("api/audit", json=body, headers={"X-LastSem-Ticket": ticket} if ticket else {})


r = post()
check("no ticket -> 401 TICKET_REQUIRED", r.status_code == 401 and r.json()["code"] == "TICKET_REQUIRED")
r = post("garbage.sig")
check("malformed ticket -> 401 TICKET_INVALID", r.status_code == 401 and r.json()["code"] == "TICKET_INVALID")
r = post(mint("wrong-secret", config.PROJECT_SLUG, 3, 15))
check("bad signature -> TICKET_INVALID", r.json()["code"] == "TICKET_INVALID")
r = post(mint(SECRET, "some-other-project", 3, 15))
check("wrong slug -> TICKET_INVALID", r.json()["code"] == "TICKET_INVALID")
r = post(mint(SECRET, config.PROJECT_SLUG, 3, -1))
check("expired -> 401 TICKET_EXPIRED", r.status_code == 401 and r.json()["code"] == "TICKET_EXPIRED")
r = post(mint(SECRET, "*", 3, 15))
check('slug "*" is accepted', r.status_code == 200)

# runs used up (also: a failed run is refunded and does not count)
t1 = mint(SECRET, config.PROJECT_SLUG, 1, 15)
fail_next["on"] = True
r = post(t1)
check("failed run -> 400 and refunded", r.status_code == 400)
info = client.get("api/info", headers={"X-LastSem-Ticket": t1}).json()
check("runs_left still 1 after the failed run", info["demo"]["runs_left"] == 1, str(info["demo"]))
fail_next["on"] = False
r = post(t1)
check("first good run -> 200", r.status_code == 200)
r = post(t1)
check("second run on a 1-run ticket -> 429 TICKET_USED_UP", r.status_code == 429 and r.json()["code"] == "TICKET_USED_UP")

# daily cap: the stub above already used 1 (slug "*") + 1 (t1) = 2 live runs today
r = post(mint(SECRET, config.PROJECT_SLUG, 5, 15))
check("daily cap of 2 reached -> 429 DAILY_CAP_REACHED", r.status_code == 429 and r.json()["code"] == "DAILY_CAP_REACHED")
info = client.get("api/info", headers={"X-LastSem-Ticket": mint(SECRET, config.PROJECT_SLUG, 5, 15)}).json()
check("/api/info reports daily_cap_reached", info["demo"]["daily_cap_reached"] is True)

# samples never need a ticket or use quota
check("sample works without a ticket", client.get("api/samples/metformin-lisinopril-control").status_code == 200)
check("key routes are 404 in demo mode", client.get("api/keys").status_code == 404)

# history is private per ticket id and deleted after 24 h
db.history_add("tid-A", "audit", "A's audit", {"x": 1})
db.history_add("tid-B", "audit", "B's audit", {"x": 2})
check("history is private per ticket id", [i["title"] for i in db.history_list("tid-A")] == ["A's audit"])
with db._lock:
    db._db().execute("UPDATE history SET ts=? WHERE client='tid-A'", (time.time() - 25 * 3600,))
    db._db().commit()
check("history older than 24 h is deleted", db.history_list("tid-A") == [] and len(db.history_list("tid-B")) == 1)

# empty secret: every ticket is invalid
config.DEMO_TICKET_SECRET = ""
check("empty DEMO_TICKET_SECRET -> every ticket invalid", post(mint(SECRET, config.PROJECT_SLUG, 3, 15)).json()["code"] == "TICKET_INVALID")

sys.exit(1 if failures else 0)
