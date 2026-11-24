"""Audit pipeline: RxNorm + openFDA evidence -> deterministic severities -> Groq plain-English explanation."""
import json
import re
import time

from . import ai_groq, clinical
from .clinical import SEVERITY_LABEL, SEVERITY_RANK
from .errors import ApiError

DISCLAIMER = ("Educational Decision-Support Only — Not Medical Advice: Always consult a licensed physician or pharmacist "
              "before starting, stopping, or changing any prescribed medication.")
CONTEXT_FLAGS = {
    "standard": ("Standard Adult", None),
    "older": ("Older Adult (65+)", "older"),
    "renal": ("Renal / Kidney Sensitivity", "renal"),
    "hepatic": ("Hepatic / Liver Sensitivity", "hepatic"),
    "pregnancy": ("Pregnancy / Breast-feeding", "pregnancy"),
    "allergy": ("Drug Allergy Flag", "allergy"),
}


def parse_context(raw):
    raw = raw if isinstance(raw, dict) else {}
    flags, labels = [], []
    for key in raw.get("flags") or []:
        if key in CONTEXT_FLAGS and key != "standard":
            flags.append(CONTEXT_FLAGS[key][1])
            labels.append(CONTEXT_FLAGS[key][0])
    custom = re.sub(r"\s+", " ", str(raw.get("custom") or "")).strip()[:300]
    if custom:
        labels.append("Custom: " + custom)
    if not labels:
        labels = ["Standard Adult"]
    return {"flags": flags, "labels": labels, "custom": custom}


def _overall(pairs):
    top = max((p["severity"] for p in pairs), key=lambda s: SEVERITY_RANK[s], default="none")
    counts = {s: sum(1 for p in pairs if p["severity"] == s) for s in SEVERITY_RANK}
    score = max(0, 100 - 35 * counts["major"] - 15 * counts["moderate"] - 5 * counts["minor"])
    verdict = {"major": "Major Interaction Flagged", "moderate": "Moderate Interaction — Monitoring Advised",
               "minor": "Low Interaction Risk — Minor Notes Only", "none": "No Known Pairwise Interaction Flag"}[top]
    return {"severity": top, "severity_label": SEVERITY_LABEL[top], "verdict": verdict, "safety_score": score, "counts": counts}


def _fallback_pair_text(p):
    if p["severity"] == "none":
        return {"why": "Neither drug's FDA label names the other (or its drug class) in the interaction, contraindication, boxed-warning or warning sections that were checked.",
                "symptoms": [], "timing": "No special spacing is described in the label sections checked.",
                "questions": ["Is there anything else I take (including over-the-counter products) that I should tell you about?"]}
    first = (p["label_hits"] or [{"text": ""}])[0]["text"]
    return {"why": "The FDA label text below flags this combination: " + first,
            "symptoms": ["See the label excerpt for the specific effects the FDA describes."],
            "timing": "Ask your pharmacist whether these two medicines should be spaced apart or monitored with blood tests.",
            "questions": [f"Is it safe for me to take {p['a'].title()} together with {p['b'].title()}?",
                          "What warning signs should I watch for, and when should I call you?"]}


def offline_explanation(pairs, overall):
    return {"summary": f"{overall['verdict']}. This summary was built directly from the FDA label text without an AI rewrite.",
            "pairs": {f"{p['a']}|{p['b']}": _fallback_pair_text(p) for p in pairs},
            "regimen_questions": ["Which of these medicines is most important to keep taking, and which could be changed?",
                                  "Do I need any blood tests or monitoring while taking this combination?"],
            "context_advice": ""}


SYSTEM = (
    "You are a clinical pharmacology explainer writing for patients. You are given verified evidence from NLM RxNorm and "
    "openFDA drug labels. Severity tiers are already decided by a deterministic matcher: NEVER change, upgrade or downgrade a severity, "
    "and NEVER invent interactions, doses or facts that are not supported by the evidence. Write in plain English at an 8th-grade level. "
    "Do not tell the patient to start, stop or change a medicine; tell them to ask a doctor or pharmacist. "
    "Reply with ONLY one JSON object, no markdown."
)


def _prompt(drugs, pairs, overall, context, notes):
    ev = []
    for p in pairs:
        if p["severity"] == "none":
            continue
        ev.append({"a": p["a"], "b": p["b"], "severity": p["severity_label"],
                   "label_excerpts": [{"from_label_of": h["source"], "section": h["section"], "text": h["text"][:300]} for h in p["label_hits"][:3]],
                   "faers_reports_with_both": p["faers"]["total"], "faers_top_reactions": [r["term"] for r in p["faers"]["top_reactions"][:4]]})
    payload = {"patient_context": context["labels"], "medicines": [d["name"] for d in drugs],
               "overall_severity": overall["severity_label"], "flagged_pairs": ev,
               "pairs_with_no_flag": [f'{p["a"]}|{p["b"]}' for p in pairs if p["severity"] == "none"],
               "boxed_warnings": {d["name"]: (d["sections"].get("boxed_warning") or "")[:350] for d in drugs if d["sections"].get("boxed_warning")},
               "context_label_notes": notes[:5]}
    return ("Evidence JSON:\n" + json.dumps(payload, ensure_ascii=False) +
            "\n\nReturn this JSON shape exactly:\n"
            '{"summary": "2-3 plain sentences about the whole regimen",'
            ' "pairs": [{"a": "drug a", "b": "drug b", "why": "why this happens in simple words (1-3 sentences)",'
            ' "symptoms": ["up to 4 symptoms to watch for"], "timing": "timing/spacing/monitoring note", "questions": ["2-3 questions for the doctor or pharmacist"]}],'
            ' "regimen_questions": ["3 questions about the whole regimen"], "context_advice": "1-2 sentences tailored to the patient context, or empty string"}\n'
            "Include one entry in pairs for every flagged pair only.")


def _validate_factory(pairs):
    flagged = {(p["a"], p["b"]) for p in pairs if p["severity"] != "none"}

    def validate(d):
        if not isinstance(d, dict) or not isinstance(d.get("summary"), str) or not d["summary"].strip():
            raise ValueError("summary missing")
        out = {}
        for item in d.get("pairs") or []:
            a, b = str(item.get("a", "")).lower().strip(), str(item.get("b", "")).lower().strip()
            key = (a, b) if (a, b) in flagged else (b, a) if (b, a) in flagged else None
            if not key or not isinstance(item.get("why"), str):
                continue
            out[f"{key[0]}|{key[1]}"] = {
                "why": item["why"].strip()[:700],
                "symptoms": [str(s).strip()[:160] for s in (item.get("symptoms") or []) if str(s).strip()][:5],
                "timing": str(item.get("timing") or "").strip()[:400],
                "questions": [str(s).strip()[:220] for s in (item.get("questions") or []) if str(s).strip()][:4]}
        if flagged and not out:
            raise ValueError("no pair explanations")
        return {"summary": d["summary"].strip()[:900], "pairs": out,
                "regimen_questions": [str(s).strip()[:220] for s in (d.get("regimen_questions") or []) if str(s).strip()][:5],
                "context_advice": str(d.get("context_advice") or "").strip()[:500]}
    return validate


async def explain(drugs, pairs, overall, context, notes, rule_based=False):
    """Return (explanation, source, model). Groq normally; rule-based only for recording samples without a key."""
    if rule_based:
        return offline_explanation(pairs, overall), "rule-based", None
    exp, model = await ai_groq.chat_json(SYSTEM, _prompt(drugs, pairs, overall, context, notes), _validate_factory(pairs), max_tokens=4000)
    return exp, "groq", model


def _assemble(drugs, pairs, context, notes, exp, source, model):
    overall = _overall(pairs)
    fb = offline_explanation(pairs, overall)
    out_pairs = []
    for p in pairs:
        key = f"{p['a']}|{p['b']}"
        text = exp["pairs"].get(key) or (_fallback_pair_text(p) if p["severity"] == "none" else fb["pairs"][key])
        out_pairs.append(dict(p, explanation=text))
    questions = []
    for p in out_pairs:
        if p["severity"] != "none":
            questions += p["explanation"]["questions"][:2]
    questions += exp.get("regimen_questions") or fb["regimen_questions"]
    seen, uniq = set(), []
    for q in questions:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return {
        "kind": "audit",
        "input": {"medications": [d["input"] for d in drugs], "context": context["labels"]},
        "overall": dict(overall, summary=exp["summary"]),
        "context_advice": exp.get("context_advice", ""),
        "drugs": [clinical.public_drug(d) for d in drugs],
        "pairs": out_pairs,
        "context_notes": notes,
        "questions": uniq[:10],
        "telemetry": {
            "rxnorm_urls": [u for d in drugs for u in d["rxnorm_urls"]],
            "label_urls": [d["label_url"] for d in drugs],
            "faers_urls": [p["faers"]["url"] for p in pairs],
            "note": "FAERS counts are spontaneous reports in which both drugs were listed. They show reporting volume, not proof of cause or how likely a reaction is.",
        },
        "source": source, "model": model,
        "disclaimer": DISCLAIMER,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


async def run_audit(items, raw_context, rule_based=False):
    context = parse_context(raw_context)
    drugs = await clinical.resolve_all(items)
    if not rule_based and not ai_groq.ai_ready():
        raise ApiError(503, "AI_NOT_CONFIGURED", ai_groq.none_message())
    pairs = await clinical.gather_evidence(drugs)
    notes = clinical.context_notes(drugs, context)
    overall = _overall(pairs)
    exp, source, model = await explain(drugs, pairs, overall, context, notes, rule_based)
    return _assemble(drugs, pairs, context, notes, exp, source, model)


def _find(drugs, name):
    n = clinical.clean_name(name or "")
    for d in drugs:
        if n and (n in d["input_variants"] or n == d["name"] or any(n == i["name"] for i in d["ingredients"])):
            return d
    return None


async def run_alternative(items, replace, alternative, raw_context, rule_based=False):
    context = parse_context(raw_context)
    if not str(replace or "").strip() or not str(alternative or "").strip():
        raise ApiError(400, "BAD_INPUT", "Choose which medicine to replace and enter the alternative you want to check.")
    if len(items) > clinical.MAX_DRUGS:
        raise ApiError(400, "BAD_INPUT", f"Enter no more than {clinical.MAX_DRUGS} medicines at a time.")
    if any(clinical.clean_name(i) == clinical.clean_name(alternative) for i in items):
        raise ApiError(400, "BAD_INPUT", "The alternative is already in the prescription list. Choose a different medicine.")
    drugs = await clinical.resolve_all(items + [str(alternative).strip()], max_n=clinical.MAX_DRUGS + 1)
    alt = _find(drugs, alternative)
    orig = _find(drugs, replace)
    if orig is None or alt is None or orig is alt:
        raise ApiError(400, "BAD_INPUT", "The medicine to replace must be one of the listed medicines, and the alternative must be a different medicine.")
    if not rule_based and not ai_groq.ai_ready():
        raise ApiError(503, "AI_NOT_CONFIGURED", ai_groq.none_message())
    all_pairs = await clinical.gather_evidence(drugs)
    names = lambda ds: {d["name"] for d in ds}
    base_drugs = [d for d in drugs if d is not alt]
    new_drugs = [d for d in drugs if d is not orig]
    sel = lambda ds: [p for p in all_pairs if p["a"] in names(ds) and p["b"] in names(ds)]
    base_pairs, new_pairs = sel(base_drugs), sel(new_drugs)
    notes = clinical.context_notes(new_drugs, context)
    base_overall, new_overall = _overall(base_pairs), _overall(new_pairs)
    exp, source, model = await explain(new_drugs, new_pairs, new_overall, context, notes, rule_based)
    proposed = _assemble(new_drugs, new_pairs, context, notes, exp, source, model)
    delta = new_overall["safety_score"] - base_overall["safety_score"]
    direction = "safer" if delta > 0 else "riskier" if delta < 0 else "unchanged"
    return {"kind": "alternative", "replace": orig["name"], "alternative": alt["name"],
            "baseline": {"overall": base_overall, "medications": [d["name"] for d in base_drugs],
                         "pairs": [{k: p[k] for k in ("a", "b", "severity", "severity_label")} for p in base_pairs]},
            "proposed": proposed,
            "comparison": {"direction": direction, "score_change": delta,
                           "headline": f"Swapping {orig['name'].title()} for {alt['name'].title()}: {base_overall['severity_label']} → {new_overall['severity_label']} ({direction})."},
            "disclaimer": DISCLAIMER, "generated_at": proposed["generated_at"]}
