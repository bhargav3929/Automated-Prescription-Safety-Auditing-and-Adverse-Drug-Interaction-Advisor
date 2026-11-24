"""Patient-context and population safety rules: reads the patient context, then raises alerts that are independent of
drug-to-drug interactions (pediatric, pregnancy, kidney/liver, allergy cross-reactivity, therapeutic duplication).

The rules are fixed lists written down here, so the same input always gives the same alert. Each alert is backed by a
sentence from the drug's own FDA label when openFDA has one, shown as evidence."""
import re

# ------------------------------------------------------------------ patient context parsing
PEDIATRIC_WORDS = re.compile(r"\b(kids?|child(?:ren)?|toddlers?|infants?|pediatric|paediatric|babys?|baby|newborns?|neonates?|schoolchild|boy|girl)\b", re.I)
PREGNANCY_WORDS = re.compile(r"pregnan|expecting|trimester|breast[- ]?feed|lactat|nursing mother|nursing", re.I)
RENAL_WORDS = re.compile(r"kidney|renal|dialysis|\bckd\b|nephro", re.I)
HEPATIC_WORDS = re.compile(r"liver|hepat|cirrhosis", re.I)
OLDER_WORDS = re.compile(r"elderly|older adult|senior|geriatric|65\+", re.I)
AGE_YEARS = re.compile(r"(\d{1,3})\s*[- ]?\s*(?:years?|yrs?|y\.?o\.?|yo)\b(?:[- ]?old)?", re.I)
AGE_MONTHS = re.compile(r"(\d{1,2})\s*[- ]?\s*(?:months?|mos?)\b(?:[- ]?old)?", re.I)
AGE_ALT = re.compile(r"\bage[d]?\s*(?:of\s*)?(\d{1,3})\b", re.I)

ALLERGY_STOP = {"a", "an", "the", "drug", "drugs", "known", "severe", "mild", "has", "have", "with", "history", "of", "to",
                "patient", "and", "or", "also", "any", "food", "seasonal", "is", "allergic", "allergy", "allergies", "bad"}
ALLERGEN_ALIASES = {  # what the patient says -> allergy class used by the rules
    "penicillin": "penicillin", "penicillins": "penicillin", "amoxicillin": "penicillin", "ampicillin": "penicillin",
    "beta-lactam": "penicillin", "betalactam": "penicillin", "augmentin": "penicillin",
    "cephalosporin": "cephalosporin", "cephalosporins": "cephalosporin", "cephalexin": "cephalosporin", "keflex": "cephalosporin",
    "sulfa": "sulfa", "sulfonamide": "sulfa", "sulfonamides": "sulfa", "sulpha": "sulfa", "bactrim": "sulfa", "sulfamethoxazole": "sulfa",
    "nsaid": "nsaid", "nsaids": "nsaid", "aspirin": "nsaid", "ibuprofen": "nsaid", "naproxen": "nsaid", "advil": "nsaid", "motrin": "nsaid",
    "salicylate": "nsaid", "salicylates": "nsaid", "diclofenac": "nsaid",
    "macrolide": "macrolide", "macrolides": "macrolide", "erythromycin": "macrolide", "azithromycin": "macrolide", "clarithromycin": "macrolide",
    "fluoroquinolone": "fluoroquinolone", "fluoroquinolones": "fluoroquinolone", "quinolone": "fluoroquinolone", "cipro": "fluoroquinolone",
    "opioid": "opioid", "opioids": "opioid", "codeine": "opioid", "morphine": "opioid",
    "statin": "statin", "statins": "statin", "ace": "ace", "lisinopril": "ace",
}


def parse_patient(raw):
    """Preset pills plus free text -> one structured patient description."""
    raw = raw if isinstance(raw, dict) else {}
    flags = {f for f in (raw.get("flags") or []) if isinstance(f, str)}
    text = re.sub(r"\s+", " ", str(raw.get("custom") or "")).strip()[:300]
    low = text.lower()
    age = None
    m = AGE_MONTHS.search(low)
    if m:
        age = int(m.group(1)) / 12.0
    else:
        m = AGE_YEARS.search(low) or AGE_ALT.search(low)
        if m and int(m.group(1)) < 120:
            age = float(m.group(1))
    if PEDIATRIC_WORDS.search(low) or (age is not None and age < 12):
        flags.add("pediatric")
    elif age is not None and age >= 12 and "pediatric" in flags and not PEDIATRIC_WORDS.search(low):
        pass  # the pill stays selected; the stated age only sharpens the age rules
    if age is not None and age >= 65:
        flags.add("older")
    if PREGNANCY_WORDS.search(low):
        flags.add("pregnancy")
    if RENAL_WORDS.search(low):
        flags.add("renal")
    if HEPATIC_WORDS.search(low):
        flags.add("hepatic")
    if OLDER_WORDS.search(low):
        flags.add("older")
    allergens = []
    if re.search(r"allerg|hypersensitiv|anaphyla", low):
        flags.add("allergy")
        chunks = re.findall(r"((?:[\w\-/+]+)(?:\s*(?:,|and|/|&)\s*[\w\-/+]+)*)\s+(?:allerg\w*|hypersensitiv\w*)", low)
        chunks += re.findall(r"(?:allerg(?:y|ies|ic)|hypersensitiv(?:e|ity))\s*(?:to|:)?\s*((?:[\w\-/+]+)(?:\s*(?:,|and|/|&)\s*[\w\-/+]+)*)", low)
        for ch in chunks:
            for w in re.split(r"\s*(?:,|and|/|&)\s*|\s+", ch):
                w = w.strip("-+ ")
                if w and w not in ALLERGY_STOP and w not in allergens:
                    allergens.append(w)
    return {"flags": flags, "age_years": age, "allergens": allergens, "custom": text, "stated_age": age is not None}


def patient_labels(patient, presets):
    labels = [presets[f] for f in presets if f in patient["flags"]]
    if patient["stated_age"]:
        a = patient["age_years"]
        labels.append("Age " + (f"{round(a * 12)} months" if a < 1 else f"{a:g} years"))
    if patient["allergens"]:
        labels.append("Allergy: " + ", ".join(patient["allergens"]))
    if patient["custom"]:
        labels.append("Custom: " + patient["custom"])
    return labels or ["Standard Adult"]


# ------------------------------------------------------------------ drug class tables (generic names)
def _g(*names):
    return set(names)

NSAIDS = _g("ibuprofen", "naproxen", "diclofenac", "ketorolac", "meloxicam", "celecoxib", "indomethacin", "etodolac", "nabumetone", "piroxicam", "ketoprofen", "aspirin")
ACE = _g("lisinopril", "enalapril", "ramipril", "captopril", "benazepril", "quinapril", "fosinopril", "perindopril", "trandolapril", "moexipril")
ARB = _g("losartan", "valsartan", "irbesartan", "olmesartan", "candesartan", "telmisartan", "azilsartan", "eprosartan")
STATINS = _g("atorvastatin", "simvastatin", "rosuvastatin", "lovastatin", "pravastatin", "pitavastatin", "fluvastatin")
FQ = _g("ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin", "norfloxacin", "gemifloxacin")
TETRA = _g("doxycycline", "tetracycline", "minocycline", "demeclocycline", "tigecycline")
PENICILLINS = _g("amoxicillin", "ampicillin", "penicillin", "penicillin v", "piperacillin", "dicloxacillin", "nafcillin", "oxacillin", "ticarcillin")
CEPHALOSPORINS = _g("cephalexin", "cefazolin", "cefuroxime", "ceftriaxone", "cefdinir", "cefixime", "cefadroxil", "cefprozil", "cefpodoxime", "cefepime", "ceftazidime")
SULFA_ABX = _g("sulfamethoxazole", "sulfadiazine", "sulfasalazine", "sulfisoxazole")
MACROLIDES = _g("clarithromycin", "erythromycin", "azithromycin")
OPIOIDS = _g("codeine", "tramadol", "morphine", "oxycodone", "hydrocodone", "hydromorphone", "fentanyl", "methadone", "tapentadol")
SSRIS = _g("sertraline", "fluoxetine", "paroxetine", "citalopram", "escitalopram", "fluvoxamine")
BENZOS = _g("alprazolam", "lorazepam", "diazepam", "clonazepam", "temazepam", "midazolam")
PPIS = _g("omeprazole", "esomeprazole", "lansoprazole", "pantoprazole", "rabeprazole")
BETABLOCKERS = _g("metoprolol", "atenolol", "propranolol", "carvedilol", "bisoprolol", "nadolol", "labetalol")
ANTICOAG = _g("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "heparin", "enoxaparin")
SULFONYLUREAS = _g("glipizide", "glyburide", "glimepiride")
THIAZIDES = _g("hydrochlorothiazide", "chlorthalidone", "indapamide", "chlorothiazide")
ANTIHISTAMINES1 = _g("diphenhydramine", "chlorpheniramine", "hydroxyzine", "promethazine", "doxylamine")
TRIPTANS = _g("sumatriptan", "rizatriptan", "zolmitriptan", "eletriptan", "naratriptan")

DUPLICATION_CLASSES = [
    ("NSAIDs (including aspirin)", NSAIDS, "Stacking NSAIDs adds up stomach ulcer, bleeding, kidney and heart risks without giving more pain relief."),
    ("ACE inhibitors", ACE, "Two ACE inhibitors double the effect on blood pressure, potassium and kidneys, including a risk of angioedema."),
    ("ARBs", ARB, "Two ARBs add up the effect on blood pressure, potassium and kidneys."),
    ("Statins", STATINS, "Two statins do not lower cholesterol much further but sharply raise the risk of muscle damage (rhabdomyolysis) and liver injury."),
    ("SSRIs", SSRIS, "Two SSRIs raise the risk of serotonin syndrome, bleeding and heart rhythm problems."),
    ("Benzodiazepines", BENZOS, "Two benzodiazepines add up sedation and breathing depression."),
    ("Opioids", OPIOIDS, "Two opioids add up sedation, constipation and dangerous slowing of breathing."),
    ("Proton pump inhibitors", PPIS, "Two PPIs give no extra benefit and add up long-term risks (low magnesium, fractures, infections)."),
    ("Beta blockers", BETABLOCKERS, "Two beta blockers can slow the heart rate and lower blood pressure too far."),
    ("Anticoagulants", ANTICOAG, "Two anticoagulants add up bleeding risk, which can be life-threatening."),
    ("Sulfonylureas", SULFONYLUREAS, "Two sulfonylureas add up the risk of severe low blood sugar."),
    ("Thiazide diuretics", THIAZIDES, "Two thiazides add up the loss of sodium and potassium and the effect on kidneys."),
    ("First-generation antihistamines", ANTIHISTAMINES1, "Stacking these adds up drowsiness, confusion and urinary retention."),
    ("Triptans", TRIPTANS, "Two triptans together can over-constrict blood vessels."),
    ("Fluoroquinolone antibiotics", FQ, "Two fluoroquinolones add up tendon, nerve and heart-rhythm risks."),
]
DUAL_RAAS = (ACE, ARB)

# ------------------------------------------------------------------ rule tables
# name -> (minimum safe age in years, why)
PEDIATRIC_RULES = {
    "metformin": (10, "The FDA label supports metformin only from age 10 (immediate-release) and not for younger children."),
    "lisinopril": (6, "The FDA label does not recommend lisinopril for children under 6 years old."),
    "aspirin": (12, "Aspirin can cause Reye's syndrome, a rare but life-threatening brain and liver illness, in children recovering from viral infections."),
    "warfarin": (12, "Warfarin has no approved pediatric dosing in the label and needs specialist monitoring in children."),
    "atorvastatin": (10, "The FDA label supports atorvastatin in children only from age 10 and only for specific inherited cholesterol conditions."),
    "codeine": (12, "The FDA says codeine must not be given to children under 12 because of life-threatening breathing problems."),
    "tramadol": (12, "The FDA says tramadol must not be given to children under 12 because of life-threatening breathing problems."),
    "promethazine": (2, "Promethazine is contraindicated under 2 years old because of fatal breathing depression."),
    "ibuprofen": (0.5, "Ibuprofen is not approved for infants under 6 months old."),
    "naproxen": (2, "Over-the-counter naproxen is not recommended for children under 12, and prescription use is limited to specific conditions."),
    "ketorolac": (17, "Ketorolac is not approved for children."),
    "simvastatin": (10, "Statins are used in children only from about age 10 and only for specific inherited cholesterol conditions."),
    "rosuvastatin": (6, "Rosuvastatin is used in children only for specific inherited cholesterol conditions and not at young ages."),
    "loperamide": (2, "Loperamide is contraindicated under 2 years old."),
    "clopidogrel": (18, "Clopidogrel has no established safety in children."),
}
for _n in FQ:
    PEDIATRIC_RULES[_n] = (18, "Fluoroquinolone antibiotics are generally avoided in children because of tendon, joint and nerve injury risks.")
for _n in TETRA:
    PEDIATRIC_RULES[_n] = (8, "Tetracycline-class antibiotics can permanently stain developing teeth and affect bone growth under 8 years old.")

PREGNANCY_RULES = {}
for _n in ACE | ARB:
    PREGNANCY_RULES[_n] = "ACE inhibitors and ARBs carry a boxed warning for fetal toxicity: they can injure or kill a developing baby."
PREGNANCY_RULES["warfarin"] = "Warfarin can cause birth defects and bleeding in the baby and is generally avoided in pregnancy."
for _n in STATINS:
    PREGNANCY_RULES[_n] = "Statins have traditionally been stopped in pregnancy because cholesterol is needed for fetal development; the decision belongs to the prescriber."
PREGNANCY_RULES.update({
    "methotrexate": "Methotrexate causes miscarriage and birth defects and is contraindicated in pregnancy.",
    "isotretinoin": "Isotretinoin causes severe birth defects and is contraindicated in pregnancy.",
    "misoprostol": "Misoprostol can cause miscarriage and birth defects.",
    "mycophenolate": "Mycophenolate causes miscarriage and birth defects.",
    "valproic acid": "Valproate causes neural tube defects and lower IQ in the baby.",
    "valproate": "Valproate causes neural tube defects and lower IQ in the baby.",
    "thalidomide": "Thalidomide causes severe birth defects.",
    "leflunomide": "Leflunomide is contraindicated in pregnancy because of birth defects.",
})
for _n in NSAIDS - {"aspirin"}:
    PREGNANCY_RULES[_n] = "NSAIDs from about 20 weeks of pregnancy can harm the baby's kidneys and heart circulation and are avoided in late pregnancy."
PREGNANCY_RULES["aspirin"] = "Pain-relief doses of aspirin are avoided in late pregnancy because of bleeding and fetal heart-circulation risks."
for _n in TETRA:
    PREGNANCY_RULES[_n] = "Tetracycline-class antibiotics can stain the baby's developing teeth and affect bone growth."

ORGAN_RULES = {  # name -> {organ: (level, why)}
    "metformin": {"renal": ("major", "Metformin builds up when kidneys work poorly and can cause lactic acidosis; the label contraindicates it in severe kidney impairment.")},
    "lithium": {"renal": ("major", "Lithium is cleared by the kidneys and becomes toxic when kidney function is reduced.")},
    "nitrofurantoin": {"renal": ("major", "Nitrofurantoin is ineffective and more toxic in significant kidney impairment.")},
    "methotrexate": {"renal": ("major", "Methotrexate is cleared by the kidneys and is much more toxic when kidney function is reduced."),
                     "hepatic": ("major", "Methotrexate can seriously injure the liver.")},
    "spironolactone": {"renal": ("major", "Spironolactone can raise potassium to dangerous levels when kidneys work poorly.")},
    "dabigatran": {"renal": ("major", "Dabigatran is cleared by the kidneys, so reduced kidney function raises bleeding risk.")},
    "gentamicin": {"renal": ("major", "Aminoglycosides such as gentamicin can damage the kidneys.")},
    "enoxaparin": {"renal": ("moderate", "Enoxaparin builds up in kidney impairment and the dose usually needs adjusting.")},
    "acetaminophen": {"hepatic": ("major", "High or repeated doses of acetaminophen can cause serious liver injury, and the safe daily dose is lower in liver disease.")},
    "valproic acid": {"hepatic": ("major", "Valproate can cause fatal liver failure.")},
    "ketoconazole": {"hepatic": ("major", "Oral ketoconazole can cause severe liver injury.")},
    "amiodarone": {"hepatic": ("major", "Amiodarone can cause liver injury.")},
    "isoniazid": {"hepatic": ("major", "Isoniazid can cause severe, sometimes fatal, liver injury.")},
    "leflunomide": {"hepatic": ("major", "Leflunomide carries a boxed warning for severe liver injury.")},
    "diclofenac": {"hepatic": ("moderate", "Diclofenac can raise liver enzymes and injure the liver.")},
    "amoxicillin/clavulanate": {"hepatic": ("moderate", "Amoxicillin-clavulanate can cause liver injury.")},
}
for _n in NSAIDS - {"aspirin"}:
    ORGAN_RULES.setdefault(_n, {})["renal"] = ("major", "NSAIDs reduce blood flow to the kidneys and can cause acute kidney injury or worsen kidney disease.")
for _n in ACE | ARB:
    ORGAN_RULES.setdefault(_n, {})["renal"] = ("major", "ACE inhibitors and ARBs can cause acute kidney injury and dangerous potassium levels when kidney function is reduced.")
for _n in STATINS:
    ORGAN_RULES.setdefault(_n, {})["hepatic"] = ("major", "Statins are used with caution in liver disease and labels advise against them in active liver disease.")

ALLERGY_CLASSES = {  # allergy class -> (drugs that match directly, drugs with possible cross-reactivity, nice name)
    "penicillin": (PENICILLINS, CEPHALOSPORINS, "penicillin"),
    "cephalosporin": (CEPHALOSPORINS, PENICILLINS, "cephalosporin"),
    "sulfa": (SULFA_ABX, _g("furosemide", "hydrochlorothiazide", "celecoxib", "glipizide", "glyburide", "glimepiride", "sumatriptan"), "sulfa (sulfonamide antibiotic)"),
    "nsaid": (NSAIDS, _g("acetaminophen"), "NSAID / aspirin"),
    "macrolide": (MACROLIDES, set(), "macrolide antibiotic"),
    "fluoroquinolone": (FQ, set(), "fluoroquinolone antibiotic"),
    "opioid": (OPIOIDS, set(), "opioid"),
    "statin": (STATINS, set(), "statin"),
    "ace": (ACE, ARB, "ACE inhibitor"),
}

SEV_LABEL = {
    "pediatric": "Major / Pediatric Contraindication", "pregnancy": "Major / Pregnancy Contraindication",
    "organ": "Major / Organ-Sensitivity Risk", "allergy": "Major / Allergy Cross-Reactivity Alert",
    "duplication": "Major / Therapeutic Duplication (Same Drug Class)",
}
MODERATE_LABEL = {"pediatric": "Moderate / Pediatric Caution", "organ": "Moderate / Organ-Sensitivity Caution",
                  "allergy": "Moderate / Possible Allergy Cross-Reactivity", "pregnancy": "Moderate / Pregnancy Caution"}
CAPS = {  # score ceiling by kind and level
    ("pediatric", "major"): 30, ("pregnancy", "major"): 25, ("organ", "major"): 45, ("allergy", "major"): 25, ("duplication", "major"): 40,
    ("pediatric", "moderate"): 60, ("organ", "moderate"): 65, ("allergy", "moderate"): 60, ("pregnancy", "moderate"): 60,
}


# ------------------------------------------------------------------ evidence from the drug's own label
def _sentences(text):
    return [p.strip() for p in re.split(r"(?<=[.;:])\s+(?=[A-Z0-9••])", text) if len(p.strip()) > 25]


def _evidence(drug, sections, pattern):
    for sec in sections:
        for s in _sentences(drug["sections"].get(sec, "")):
            m = re.search(pattern, s, re.I)
            if m:
                pos = max(m.start() - 160, 0)
                snip = s if len(s) <= 360 else ("…" if pos else "") + s[pos:pos + 360].strip() + "…"
                return {"drug": drug["name"], "section": sec, "text": snip}
    return None


def _ids(d):
    return {i["name"] for i in d["ingredients"]} | {d["name"]}


def _alert(kind, level, drugs, title, detail, evidence=None):
    label = (SEV_LABEL if level == "major" else MODERATE_LABEL)[kind]
    return {"id": f"{kind}:{'+'.join(sorted(drugs))}", "kind": kind, "severity": level, "label": label, "drugs": sorted(drugs),
            "title": title, "detail": detail, "evidence": [e for e in (evidence or []) if e], "score_cap": CAPS[(kind, level)]}


def _age_text(patient):
    a = patient["age_years"]
    if a is None:
        return "age not stated, so the strictest child rule is applied"
    return f"{round(a * 12)} months old" if a < 1 else f"{a:g} years old"


# ------------------------------------------------------------------ the rule checks
def evaluate(drugs, patient):
    """Return the list of patient-context and duplication alerts for these resolved drugs."""
    alerts = []
    flags = patient["flags"]
    age = patient["age_years"]
    names = {d["name"]: d for d in drugs}

    if "pediatric" in flags and (age is None or age < 12):
        for d in drugs:
            hit = next(((n, PEDIATRIC_RULES[n]) for n in _ids(d) if n in PEDIATRIC_RULES), None)
            ev = _evidence(d, ["pediatric_use", "contraindications", "warnings", "warnings_and_cautions", "boxed_warning"],
                           r"pediatric|children|child|infant|neonat|reye")
            if hit and (age is None or age < hit[1][0]):
                alerts.append(_alert("pediatric", "major", [d["name"]], f"{d['name'].title()} is not appropriate for this child",
                                     f"{hit[1][1]} Patient: {_age_text(patient)}.", [ev]))
            elif not hit and ev:
                ptxt = d["sections"].get("pediatric_use", "")
                if re.search(r"contraindicat|should not be (?:used|given)|do not (?:use|give)", ptxt, re.I):
                    alerts.append(_alert("pediatric", "major", [d["name"]], f"The {d['name'].title()} label restricts use in children",
                                         f"The FDA label for {d['name']} says it should not be used in some young children. Patient: {_age_text(patient)}.", [ev]))
                elif re.search(r"not (?:been )?(?:established|recommended)|have not been studied|no data", ptxt, re.I):
                    alerts.append(_alert("pediatric", "moderate", [d["name"]], f"Safety of {d['name'].title()} in children is not established",
                                         f"The FDA label says safety and effectiveness in children have not been established or recommended. Patient: {_age_text(patient)}.", [ev]))

    if "pregnancy" in flags:
        for d in drugs:
            hit = next((PREGNANCY_RULES[n] for n in _ids(d) if n in PREGNANCY_RULES), None)
            if hit:
                ev = _evidence(d, ["boxed_warning", "contraindications", "pregnancy", "pregnancy_or_breast_feeding", "use_in_specific_populations", "warnings_and_cautions", "warnings"],
                               r"pregnan|fetal|fetus|teratogen|birth defect")
                alerts.append(_alert("pregnancy", "major", [d["name"]], f"{d['name'].title()} is a pregnancy / breast-feeding concern", hit, [ev]))

    for organ, flag in (("renal", "renal"), ("hepatic", "hepatic")):
        if flag in flags:
            for d in drugs:
                rule = next((ORGAN_RULES[n][organ] for n in _ids(d) if n in ORGAN_RULES and organ in ORGAN_RULES[n]), None)
                if rule:
                    pat = r"renal|kidney|egfr|creatinine" if organ == "renal" else r"hepatic|liver|hepatotox|cirrhosis"
                    ev = _evidence(d, ["boxed_warning", "contraindications", "warnings_and_cautions", "warnings", "use_in_specific_populations"], pat)
                    where = "Kidney" if organ == "renal" else "Liver"
                    alerts.append(_alert("organ", rule[0], [d["name"]], f"{d['name'].title()} with {where.lower()} sensitivity", rule[1], [ev]))

    if patient["allergens"] or "allergy" in flags:
        for allergen in patient["allergens"]:
            cls = ALLERGEN_ALIASES.get(allergen)
            for d in drugs:
                ids = _ids(d)
                if allergen in ids or any(allergen in n or n in allergen for n in ids if len(n) > 4):
                    ev = _evidence(d, ["contraindications", "warnings", "warnings_and_cautions"], r"hypersensitiv|allerg|anaphyla")
                    alerts.append(_alert("allergy", "major", [d["name"]], f"Patient is allergic to {allergen}",
                                         f"The patient reports an allergy to {allergen}, and {d['name']} is that medicine.", [ev]))
                    continue
                if cls and cls in ALLERGY_CLASSES:
                    direct, cross, nice = ALLERGY_CLASSES[cls]
                    if ids & direct:
                        ev = _evidence(d, ["contraindications", "warnings", "warnings_and_cautions"], r"hypersensitiv|allerg|anaphyla|cross")
                        alerts.append(_alert("allergy", "major", [d["name"]], f"{d['name'].title()} belongs to the class the patient is allergic to",
                                             f"The patient reports a {allergen} allergy and {d['name']} is a {nice}-class medicine, so a serious allergic reaction is possible.", [ev]))
                    elif ids & cross:
                        alerts.append(_alert("allergy", "moderate", [d["name"]], f"{d['name'].title()} may cross-react with a {allergen} allergy",
                                             f"People allergic to {allergen} sometimes also react to {d['name']}. Ask the prescriber or pharmacist before taking it.", []))

    # therapeutic duplication (always on, whatever the patient context)
    present = {d["name"] for d in drugs}
    shown = set()
    for cname, members, why in DUPLICATION_CLASSES:
        group = sorted(n for d in drugs for n in [d["name"]] if (_ids(d) & members))
        if len(set(group)) >= 2:
            key = tuple(sorted(set(group)))
            shown.add(key)
            alerts.append(_alert("duplication", "major", list(set(group)),
                                 f"Therapeutic duplication: {' + '.join(n.title() for n in key)} are both {cname}", why + " Ask the prescriber whether both are really needed.", []))
    if any(_ids(d) & ACE for d in drugs) and any(_ids(d) & ARB for d in drugs):
        pair = sorted(d["name"] for d in drugs if _ids(d) & (ACE | ARB))
        alerts.append(_alert("duplication", "major", pair, "Dual RAAS blockade: ACE inhibitor + ARB",
                             "Combining an ACE inhibitor with an ARB raises the risk of kidney injury, high potassium and low blood pressure without clear benefit.", []))
    # same label EPC class not covered above (specific classes only)
    skip = re.compile(r"antibacterial|antibiotic|anti-?infective|antiviral|antifungal|vitamin|mineral|supplement|hormone|analgesic$|diuretic$|^other", re.I)
    by_epc = {}
    for d in drugs:
        for e in d["label"].get("openfda", {}).get("pharm_class_epc") or []:
            if not skip.search(e.replace(" [EPC]", "")):
                by_epc.setdefault(e, set()).add(d["name"])
    for e, members in by_epc.items():
        if len(members) >= 2 and not any(set(k) >= members or members >= set(k) for k in shown):
            key = tuple(sorted(members))
            shown.add(key)
            alerts.append(_alert("duplication", "major", list(members),
                                 f"Therapeutic duplication: {' + '.join(n.title() for n in key)} share the FDA class “{e.replace(' [EPC]', '')}”",
                                 "Two medicines from the same drug class usually add up the same side effects without more benefit. Ask the prescriber whether both are needed.", []))

    # de-duplicate by id
    uniq, seen = [], set()
    for a in alerts:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    rank = {"major": 0, "moderate": 1}
    uniq.sort(key=lambda a: (rank[a["severity"]], a["kind"]))
    return uniq
