"""Record real outputs for every bundled sample into samples/results/*.json.

Runs the same code path as POST /api/audit: live RxNorm + openFDA evidence, then the Groq clinical explainer.
With a usable Groq key (saved in the app or GROQ_API_KEY in .env) the explanations come from Groq.
Without one, pass --rule-based to record the live RxNorm/openFDA evidence with explanations built straight
from the FDA label text (marked "source": "rule-based" so it is never mistaken for an AI answer).

    .venv/Scripts/python scripts/record_samples.py [--rule-based] [sample-id ...]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai_groq, clinical, config, db, pipeline  # noqa: E402
from app.errors import ApiError  # noqa: E402


async def main(rule_based, only):
    db.init()
    out_dir = config.SAMPLES_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    if not rule_based and not ai_groq.ai_ready():
        sys.exit("No usable Groq key found. Add one in the app (API Keys) or .env, or re-run with --rule-based.")
    for path in sorted(config.SAMPLES_DIR.glob("*.json")):
        s = json.loads(path.read_text(encoding="utf-8"))
        if only and s["id"] not in only:
            continue
        items = clinical.split_medications(None, s["input_text"])
        try:
            result = await pipeline.run_audit(items, s.get("context"), rule_based=rule_based)
        except ApiError as e:
            if not s.get("expect_error"):
                raise
            payload = {"kind": "rejection", "input": {"medications": s["input_text"]}, "http_status": e.status, "code": e.code,
                       "error": e.message, "quota_consumed": False, "advice_returned": False,
                       "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            (out_dir / f"{s['id']}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f'{s["id"]}: rejected {e.status} {e.code}: {e.message}')
            continue
        if s.get("expect_error"):
            sys.exit(f'{s["id"]} was expected to be rejected but was audited')
        (out_dir / f"{s['id']}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f'{s["id"]}: {result["overall"]["severity_label"]} (safety {result["overall"]["safety_score"]}) source={result["source"]}')


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main("--rule-based" in sys.argv, set(args)))
