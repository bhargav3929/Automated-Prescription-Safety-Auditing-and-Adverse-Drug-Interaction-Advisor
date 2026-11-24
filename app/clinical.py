"""RxNorm name normalisation, openFDA label + FAERS lookups and deterministic pairwise cross-matching."""
import asyncio
import itertools
import re
from urllib.parse import urlencode

import httpx

from . import config, db
from .errors import ApiError

TIMEOUT_S = 20
TTL_LABEL = 7 * 86400
TTL_EVENT = 86400
MIN_DRUGS, MAX_DRUGS = 2, 8
SECTIONS = ["boxed_warning", "contraindications", "drug_interactions", "warnings", "warnings_and_cautions"]
SEVERITY_RANK = {"none": 0, "minor": 1, "moderate": 2, "major": 3}
SEVERITY_LABEL = {"major": "Major / Contraindicated", "moderate": "Moderate / Monitor Closely",
                  "minor": "Minor / Low Risk", "none": "No Known Pairwise Flag"}
UNVERIFIED = "Couldn't verify one or more medication names in RxNorm/openFDA"

# Class words that FDA labels use when they talk about a whole group of drugs.
NSAIDS = ["nsaid", "nonsteroidal", "non-steroidal", "anti-inflammatory drug"]
CLASS_TERMS = {
    "warfarin": ["anticoagulant", "coumarin", "vitamin k antagonist"],
    "aspirin": ["salicylate", "antiplatelet", "platelet aggregation inhibitor", "platelet inhibitor"] + NSAIDS,
    "ibuprofen": NSAIDS + ["propionic acid"],
    "naproxen": NSAIDS, "diclofenac": NSAIDS, "ketorolac": NSAIDS, "meloxicam": NSAIDS, "celecoxib": NSAIDS,
    "clarithromycin": ["macrolide", "strong cyp3a", "cyp3a4 inhibitor", "cyp3a inhibitor"],
    "erythromycin": ["macrolide", "cyp3a4 inhibitor"],
    "atorvastatin": ["statin", "hmg-coa reductase inhibitor", "hmg-coa"],
    "simvastatin": ["statin", "hmg-coa reductase inhibitor"], "rosuvastatin": ["statin", "hmg-coa reductase inhibitor"],
    "lisinopril": ["ace inhibitor", "angiotensin converting enzyme", "angiotensin-converting enzyme", "angiotensin"],
    "enalapril": ["ace inhibitor", "angiotensin converting enzyme", "angiotensin-converting enzyme"],
    "metformin": ["biguanide", "antidiabetic", "antihyperglycemic", "hypoglycemic agent"],
    "insulin": ["antidiabetic", "hypoglycemic agent"],
}
STRONG = re.compile(r"contraindicat|do not (?:co-?administer|use|take)|should not be (?:used|co-?administered|given)|avoid|"
                    r"life-threatening|fatal|serious|rhabdomyolysis|hemorrhag|haemorrhag|major bleeding|"
                    r"increased? (?:the )?risk of (?:serious )?(?:bleeding|myopathy)|torsade|qt prolong", re.I)
MODERATE = re.compile(r"monitor|adjust|caution|may (?:increase|decrease|enhance|reduce|potentiate)|increase[sd]?|"
                      r"decrease[sd]?|reduce[sd]?|enhance|potentiat|dose|inr|prothrombin|interact", re.I)
NOISE_WORDS = re.compile(r"\b(tablets?|capsules?|caps?|tabs?|oral|solution|injection|daily|once|twice|bid|tid|qd|qid|prn|"
                         r"extended[- ]release|er|xr|sr|film[- ]coated|chewable|delayed[- ]release|dr|mg/ml)\b", re.I)
STRENGTH = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|ug|g|ml|iu|units?|%)(?:/\w+)?", re.I)

ALLERGY = re.compile(r"hypersensitiv|allerg|asthma|urticaria|angioedema|anaphyla|bronchospasm|rhinitis|nasal polyps", re.I)

_event_lock = asyncio.Semaphore(4)


# ------------------------------------------------------------------ http helpers
async def _get(client, url, params=None, ttl=TTL_LABEL, ok_missing=False):
    """GET JSON with a sqlite cache and 3 attempts. Returns (status, body). 404 from openFDA means 'no matches'."""
    full = url + ("?" + urlencode(params) if params else "")
    hit = db.cache_get(full)
    if hit and hit[2] < ttl:
        return hit[0], hit[1]
    last = None
    for attempt in range(3):
        try:
            resp = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = type(exc).__name__
            await asyncio.sleep(0.6 * (attempt + 1))
            continue
        if resp.status_code in (200, 404):
            try:
                body = resp.json()
            except ValueError:
                body = {}
            db.cache_set(full, resp.status_code, body)
            return resp.status_code, body
        last = resp.status_code
        await asyncio.sleep(0.8 * (attempt + 1))
    if ok_missing:
        return None, {}
    raise ApiError(503, "AI_BUSY", "The public RxNorm/openFDA drug databases are busy right now. Wait a minute and try again.")


def full_url(url, params):
    return url + ("?" + urlencode(params) if params else "")


# ------------------------------------------------------------------ input parsing
def split_medications(medications=None, text=None):
    items = []
    if isinstance(medications, list):
        items += [str(m) for m in medications if isinstance(m, (str, int, float))]
    if isinstance(text, str) and text.strip():
        items += re.split(r"[,;\n\r|]+|\s\+\s|\splus\s", text)
    out, seen = [], set()
    for raw in items:
        raw = re.sub(r"^[\s\-\*•\d.)]+(?=[A-Za-z])", "", raw.strip())
        if not raw:
            continue
        if len(raw) > 80:
            raise ApiError(400, "BAD_INPUT", "Each medicine name must be shorter than 80 characters.")
        out.append(raw)
    for raw in out:
        seen.add(raw.lower())
    return out


def clean_name(raw):
    s = STRENGTH.sub(" ", raw)
    s = re.sub(r"(?<=[A-Za-z])\d+(?:[.,]\d+)?(?:mg|mcg|g|ml|iu)?\b", " ", s, flags=re.I)
    s = NOISE_WORDS.sub(" ", s)
    s = re.sub(r"[^A-Za-z\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


# ------------------------------------------------------------------ RxNorm
def _rx(path):
    return f"{config.RXNORM_API_BASE}/{path}"


async def resolve_rxnorm(client, raw):
    """Return {rxcui, rxnorm_name, tty, ingredients:[{rxcui,name}], urls:[...]} or None if RxNorm doesn't know it."""
    name = clean_name(raw)
    if len(name) < 3:
        return None
    urls = []
    rxcui = None
    params = {"name": name, "search": 2}
    status, body = await _get(client, _rx("rxcui.json"), params)
    urls.append(full_url(_rx("rxcui.json"), params))
    ids = (body.get("idGroup") or {}).get("rxnormId") or []
    if ids:
        rxcui = ids[0]
    else:
        params = {"term": name, "maxEntries": 3}
        status, body = await _get(client, _rx("approximateTerm.json"), params)
        urls.append(full_url(_rx("approximateTerm.json"), params))
        cands = ((body.get("approximateGroup") or {}).get("candidate")) or []
        good = [c for c in cands if str(c.get("score", "0")).replace(".", "").isdigit() and float(c["score"]) >= 75]
        if good:
            rxcui = good[0]["rxcui"]
    if not rxcui:
        return None
    pp = _rx(f"rxcui/{rxcui}/properties.json")
    status, body = await _get(client, pp)
    urls.append(pp)
    props = body.get("properties") or {}
    if not props:
        return None
    tty = props.get("tty", "")
    ingredients = []
    if tty in ("IN", "MIN", "PIN"):
        ingredients = [{"rxcui": rxcui, "name": props.get("name", name).lower()}]
    else:
        rp = _rx(f"rxcui/{rxcui}/related.json")
        rparams = {"tty": "IN"}
        status, body = await _get(client, rp, rparams)
        urls.append(full_url(rp, rparams))
        for grp in (body.get("relatedGroup") or {}).get("conceptGroup") or []:
            for c in grp.get("conceptProperties") or []:
                ingredients.append({"rxcui": c["rxcui"], "name": c["name"].lower()})
    if not ingredients:
        ingredients = [{"rxcui": rxcui, "name": props.get("name", name).lower()}]
    if tty == "MIN":  # multi-ingredient: "a / b / c"
        ingredients = [{"rxcui": rxcui, "name": n.strip().lower()} for n in props.get("name", "").split("/") if n.strip()][:4] or ingredients
    return {"rxcui": rxcui, "rxnorm_name": props.get("name", name), "tty": tty, "ingredients": ingredients[:4], "urls": urls}


# ------------------------------------------------------------------ openFDA
def _fda(path):
    return f"{config.OPENFDA_API_BASE}/{path}"


async def fetch_label(client, ingredient):
    """Return (label_or_None, url). Prefers a label that actually has a drug_interactions section."""
    url = _fda("label.json")
    for field in ("openfda.generic_name", "openfda.substance_name"):
        params = {"search": f'{field}:"{ingredient}"', "limit": 5}
        status, body = await _get(client, url, params)
        results = body.get("results") or []
        if results:
            results.sort(key=lambda r: (not r.get("drug_interactions"), not r.get("boxed_warning") and 0, len(" ".join(r.get("openfda", {}).get("generic_name", [""])))))
            return results[0], full_url(url, params)
    return None, full_url(url, {"search": f'openfda.generic_name:"{ingredient}"', "limit": 5})


def _terms_for(drug, label):
    names = {i["name"] for i in drug["ingredients"]} | {drug["name"], drug["rxnorm_name"].lower()}
    names |= {n.lower() for n in drug.get("input_variants", [])}
    if label:
        of = label.get("openfda", {})
        names |= {b.lower() for b in (of.get("brand_name") or [])[:6]}
        names |= {g.lower() for g in (of.get("generic_name") or [])[:3] if len(g) < 40}
    terms = set()
    for n in names:
        n = re.sub(r"\s+(tablets?|capsules?|oral.*)$", "", n).strip()
        if len(n) >= 4:
            terms.add(n)
            terms.add(n.split()[0]) if len(n.split()[0]) >= 5 else None
    classes = set()
    for ing in drug["ingredients"]:
        classes |= set(CLASS_TERMS.get(ing["name"], []))
    return sorted(terms), sorted(classes)


def _section_texts(label):
    out = {}
    for sec in SECTIONS + ["geriatric_use"]:
        v = label.get(sec) if label else None
        if v:
            out[sec] = re.sub(r"\s+", " ", " ".join(v) if isinstance(v, list) else str(v)).strip()
    return out


def _sentences(text):
    parts = re.split(r"(?<=[.;:])\s+(?=[A-Z0-9••])", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def _window(s, term, size=420):
    """Trim a long label sentence (some are whole tables) to a window around the matched term."""
    if len(s) <= size:
        return s
    pos = max(s.lower().find(term), 0)
    start = max(0, pos - size // 2)
    end = min(len(s), start + size)
    return ("…" if start else "") + s[start:end].strip() + ("…" if end < len(s) else "")


def _find_hits(sections, terms, classes):
    """Sentences in `sections` that mention any of `terms` (direct) or `classes` (class-level)."""
    hits = []
    for sec in ("boxed_warning", "contraindications", "drug_interactions", "warnings", "warnings_and_cautions"):
        text = sections.get(sec)
        if not text:
            continue
        for s in _sentences(text):
            low = s.lower()
            if sec != "drug_interactions" and ALLERGY.search(s):
                continue  # allergy / sensitivity wording is not a drug-drug interaction
            direct = [t for t in terms if re.search(r"\b" + re.escape(t) + r"\b", low)]
            klass = [c for c in classes if c in low]
            if direct or klass:
                term = (direct or klass)[0]
                hits.append({"section": sec, "match": "direct" if direct else "class", "term": term, "text": _window(s, term), "_full": s})
    # keep the most informative few per section
    seen, kept = set(), []
    for h in sorted(hits, key=lambda h: (h["match"] != "direct", ["boxed_warning", "contraindications", "drug_interactions"].index(h["section"]) if h["section"] in ("boxed_warning", "contraindications", "drug_interactions") else 3)):
        if h["text"][:80] in seen:
            continue
        seen.add(h["text"][:80])
        kept.append(h)
    return kept[:5]


def rate_pair(hits_ab, hits_ba):
    """Deterministic severity from where the label mentions the other drug and how it words the warning."""
    best = "none"
    reasons = []
    for owner, hits in (("a", hits_ab), ("b", hits_ba)):
        for h in hits:
            text = h.get("_full", h["text"])
            strong = bool(STRONG.search(text))
            mod = bool(MODERATE.search(text))
            if h["section"] in ("contraindications", "boxed_warning") and h["match"] == "direct":
                level = "major"
            elif h["section"] == "contraindications" and strong:
                level = "major"
            elif h["match"] == "direct" and strong and h["section"] in ("drug_interactions", "boxed_warning"):
                level = "major"
            elif h["match"] == "direct" and mod:
                level = "moderate"
            elif h["match"] == "class" and strong and h["section"] != "warnings":
                level = "moderate"
            elif h["match"] == "direct":
                level = "moderate" if strong else "minor"
            else:
                level = "minor"
            if SEVERITY_RANK[level] > SEVERITY_RANK[best]:
                best = level
                reasons = [f"{h['section'].replace('_', ' ')} wording ({h['match']} mention of “{h['term']}”)"]
    return best, reasons


async def faers_pair(client, a, b):
    url = _fda("event.json")
    search = f'patient.drug.openfda.generic_name:"{a}" AND patient.drug.openfda.generic_name:"{b}"'
    tparams = {"search": search, "limit": 1}
    cparams = {"search": search, "count": "patient.reaction.reactionmeddrapt.exact"}
    async with _event_lock:
        status, body = await _get(client, url, tparams, ttl=TTL_EVENT, ok_missing=True)
        total = None
        if status == 200:
            total = ((body.get("meta") or {}).get("results") or {}).get("total")
        elif status == 404:
            total = 0
        reactions = []
        if total:
            s2, b2 = await _get(client, url, cparams, ttl=TTL_EVENT, ok_missing=True)
            reactions = [{"term": r["term"].title(), "count": r["count"]} for r in (b2.get("results") or [])[:5]]
    return {"total": total, "top_reactions": reactions, "url": full_url(url, tparams), "reactions_url": full_url(url, cparams)}


# ------------------------------------------------------------------ the audit evidence pipeline
async def resolve_all(items, max_n=MAX_DRUGS):
    """Normalise each entered name through RxNorm, confirm an openFDA label exists. Raises BAD_INPUT."""
    if len(items) < MIN_DRUGS:
        raise ApiError(400, "BAD_INPUT", "Enter at least two medicines to audit for interactions.")
    if len(items) > max_n:
        raise ApiError(400, "BAD_INPUT", f"Enter no more than {MAX_DRUGS} medicines at a time.")
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers={"Accept": "application/json", "User-Agent": "prescription-safety-advisor/1.0"}) as client:
        resolved = await asyncio.gather(*[resolve_rxnorm(client, raw) for raw in items])
        bad = [raw for raw, r in zip(items, resolved) if r is None]
        if bad:
            raise ApiError(400, "BAD_INPUT", f"{UNVERIFIED}: {', '.join(bad)}. Check the spelling and try again.")
        drugs, seen = [], {}
        for raw, r in zip(items, resolved):
            key = tuple(sorted(i["rxcui"] for i in r["ingredients"]))
            if key in seen:
                seen[key]["input_variants"].append(raw.lower())
                continue
            d = {"input": raw, "rxcui": r["rxcui"], "rxnorm_name": r["rxnorm_name"], "tty": r["tty"],
                 "ingredients": r["ingredients"], "name": r["ingredients"][0]["name"], "rxnorm_urls": r["urls"], "input_variants": [clean_name(raw)]}
            seen[key] = d
            drugs.append(d)
        if len(drugs) < MIN_DRUGS:
            raise ApiError(400, "BAD_INPUT", "Those entries are the same medicine. Enter at least two different medicines.")
        labels = await asyncio.gather(*[fetch_label(client, d["name"]) for d in drugs])
        bad = [d["input"] for d, (lab, _) in zip(drugs, labels) if lab is None]
        if bad:
            raise ApiError(400, "BAD_INPUT", f"{UNVERIFIED}: {', '.join(bad)}. No FDA drug label was found for it.")
        for d, (lab, url) in zip(drugs, labels):
            d["label_url"] = url
            d["label"] = lab
        return drugs


async def gather_evidence(drugs):
    """Pairwise label cross-match + FAERS co-report counts for already-resolved drugs."""
    for d in drugs:
        d["sections"] = _section_texts(d["label"])
        d["terms"], d["classes"] = _terms_for(d, d["label"])
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers={"Accept": "application/json", "User-Agent": "prescription-safety-advisor/1.0"}) as client:
        combos = list(itertools.combinations(range(len(drugs)), 2))
        faers = await asyncio.gather(*[faers_pair(client, drugs[i]["name"], drugs[j]["name"]) for i, j in combos])
    pairs = []
    for (i, j), fa in zip(combos, faers):
        a, b = drugs[i], drugs[j]
        hits_ab = _find_hits(a["sections"], b["terms"], b["classes"])  # A's label talks about B
        hits_ba = _find_hits(b["sections"], a["terms"], a["classes"])  # B's label talks about A
        severity, reasons = rate_pair(hits_ab, hits_ba)
        for h in hits_ab + hits_ba:
            h.pop("_full", None)
        pairs.append({"a": a["name"], "b": b["name"], "severity": severity, "severity_label": SEVERITY_LABEL[severity],
                      "basis": reasons, "label_hits": [dict(h, source=a["name"]) for h in hits_ab] + [dict(h, source=b["name"]) for h in hits_ba],
                      "faers": fa})
    return pairs


def context_notes(drugs, context):
    """Label sentences relevant to the patient context (kidney, liver, older adult, pregnancy, allergy)."""
    ctx = (context or {}).get("flags") or []
    words = {"renal": r"renal|kidney", "hepatic": r"hepatic|liver", "older": r"geriatric|elderly|older|65 years", "pregnancy": r"pregnan|fetal|lactat", "allergy": r"allerg|hypersensitiv|anaphyla|angioedema"}
    notes = []
    for d in drugs:
        for flag in ctx:
            pat = words.get(flag)
            if not pat:
                continue
            sec_order = ["boxed_warning", "contraindications", "warnings_and_cautions", "warnings", "geriatric_use"]
            for sec in sec_order:
                text = d["sections"].get(sec, "")
                sent = next((s for s in _sentences(text) if re.search(pat, s, re.I)), None)
                if sent:
                    notes.append({"drug": d["name"], "context": flag, "section": sec, "text": sent[:380]})
                    break
    return notes[:8]


def public_drug(d):
    sec = d["sections"]
    return {"input": d["input"], "name": d["name"], "rxcui": d["rxcui"], "rxnorm_name": d["rxnorm_name"],
            "ingredients": d["ingredients"], "tty": d["tty"],
            "boxed_warning": (sec.get("boxed_warning") or "")[:600] or None,
            "sections_found": [s for s in SECTIONS if s in sec],
            "brand_names": (d["label"].get("openfda", {}).get("brand_name") or [])[:4],
            "rxnorm_urls": d["rxnorm_urls"], "label_url": d["label_url"]}
