"""Check every curated rule in app/audit.py against the drug's own openFDA label text.

For each pediatric, pregnancy, kidney and liver rule it looks for supporting words in the label sections that
openFDA returns. A rule with no support is printed so it can be reviewed or removed. This does not replace a
pharmacist's review; it only makes sure no rule is invented.

    .venv/Scripts/python scripts/verify_rules.py
"""
import asyncio
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audit, clinical, db  # noqa: E402

PEDS_KEYWORDS = {
    "metformin": r"\b10 years|pediatric",
    "lisinopril": r"6 years|pediatric",
    "aspirin": r"reye",
    "warfarin": r"pediatric",
    "atorvastatin": r"10 years|pediatric",
    "codeine": r"12 years|pediatric",
    "tramadol": r"12 years|pediatric",
    "promethazine": r"2 years",
    "ibuprofen": r"6 months|pediatric",
    "naproxen": r"pediatric|children",
    "ketorolac": r"pediatric",
    "simvastatin": r"10 years|pediatric",
    "rosuvastatin": r"pediatric",
    "loperamide": r"2 years|pediatric",
    "clopidogrel": r"pediatric",
}
PEDS_CLASS = {"fq": (audit.FQ, r"pediatric|children|tendon"), "tetra": (audit.TETRA, r"8 years|tooth|teeth|pediatric|children")}
PREG_PATTERN = r"pregnan|fetal|fetus|teratogen|birth defect|neural tube"
ORGAN_PATTERN = {"renal": r"renal|kidney|egfr|creatinine", "hepatic": r"hepatic|liver|hepatotox|cirrhosis"}
SECS = ["boxed_warning", "contraindications", "pediatric_use", "pregnancy", "pregnancy_or_breast_feeding", "use_in_specific_populations",
        "warnings", "warnings_and_cautions", "nursing_mothers", "drug_interactions", "teratogenic_effects"]


async def main():
    db.init()
    names = set(audit.PEDIATRIC_RULES) | set(audit.PREGNANCY_RULES) | set(audit.ORGAN_RULES)
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "prescription-safety-advisor/1.0"}) as c:
        labels = {}
        sem = asyncio.Semaphore(4)

        async def get(n):
            async with sem:
                try:
                    lab, _ = await clinical.fetch_label(c, n)
                except Exception:
                    lab = None
                labels[n] = clinical._section_texts(lab) if lab else None

        await asyncio.gather(*[get(n) for n in sorted(names)])
    supported = unsupported = nolabel = 0
    report = []

    def judge(kind, name, pattern):
        nonlocal supported, unsupported, nolabel
        secs = labels.get(name)
        if secs is None:
            nolabel += 1
            report.append(f"NO LABEL   {kind:9} {name}")
            return
        text = " ".join(secs.get(s, "") for s in SECS)
        if re.search(pattern, text, re.I):
            supported += 1
        else:
            unsupported += 1
            report.append(f"UNSUPPORTED {kind:9} {name}: label has no sentence matching /{pattern}/")

    for n in audit.PEDIATRIC_RULES:
        pat = PEDS_KEYWORDS.get(n) or next((p for _, (members, p) in PEDS_CLASS.items() if n in members), r"pediatric|children")
        judge("pediatric", n, pat)
    for n in audit.PREGNANCY_RULES:
        judge("pregnancy", n, PREG_PATTERN)
    for n, organs in audit.ORGAN_RULES.items():
        for organ in organs:
            judge(organ, n, ORGAN_PATTERN[organ])
    print("\n".join(sorted(report)))
    print(f"\nrules checked against FDA label text: {supported} supported, {unsupported} unsupported, {nolabel} with no openFDA label found")


asyncio.run(main())
