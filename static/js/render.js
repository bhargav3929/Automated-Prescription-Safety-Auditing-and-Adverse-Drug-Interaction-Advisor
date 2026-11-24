/* Builds the clinical-monograph results canvas from an audit result. All FDA text goes in via textContent. */
const Render = (() => {
  const SEV = { major: "Major / Contraindicated", moderate: "Moderate / Monitor Closely", minor: "Minor / Low Risk", none: "No Known Pairwise Flag" };
  const SEC = { boxed_warning: "Boxed warning", contraindications: "Contraindications", drug_interactions: "Drug interactions", warnings: "Warnings", warnings_and_cautions: "Warnings and cautions", geriatric_use: "Geriatric use" };
  const cap = (s) => (s || "").replace(/\b\w/g, (c) => c.toUpperCase());

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  const kids = (parent, ...c) => { c.filter(Boolean).forEach((x) => parent.appendChild(x)); return parent; };
  const badge = (sev, text) => el("span", "badge sev-" + sev, text || SEV[sev]);
  const list = (items, cls) => { const ul = el("ul", cls); items.forEach((t) => ul.appendChild(el("li", null, t))); return ul; };
  const num = (n) => (n === null || n === undefined ? "n/a" : Number(n).toLocaleString());

  function link(url) {
    const a = el("a", "mono url", url);
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    return a;
  }

  function verdict(r) {
    const o = r.overall;
    const card = el("div", "verdict sev-" + o.severity);
    const left = kids(el("div", "v-main"), el("div", "eyebrow", "Highest severity verdict"), el("h2", null, o.verdict), badge(o.severity, o.severity_label), el("p", "summary", o.summary));
    if (r.patient && r.patient.labels) left.appendChild(el("p", "ctx-line mono", "Patient context: " + r.patient.labels.join(" · ")));
    if (r.context_advice) left.appendChild(el("p", "ctx-advice", r.context_advice));
    const score = kids(el("div", "score"), el("div", "score-num mono", String(o.safety_score)), el("div", "score-cap", "Regimen safety score / 100"),
      el("div", "score-counts mono", `${o.counts.major} major · ${o.counts.moderate} moderate · ${o.counts.minor} minor pairs` + (o.alert_counts ? ` · ${o.alert_counts.major + o.alert_counts.moderate} alerts` : "")));
    kids(card, left, score);
    return card;
  }

  function alertsSection(r) {
    const list_ = r.alerts || [];
    if (!list_.length) return null;
    const box = el("div", "sheet alerts sev-" + r.overall.severity);
    box.appendChild(kids(el("div", "sheet-head"), el("h2", null, "Patient-Context & Safety Rule Alerts"),
      el("span", "muted", "These rules apply to the patient, not just to drug-to-drug pairs, and they lower the safety score.")));
    const pills = el("div", "alert-pills");
    list_.forEach((a) => {
      const d = el("details", "alert-pill sev-" + a.severity);
      const sum = el("summary");
      sum.appendChild(el("span", "alert-tag", a.label));
      sum.appendChild(el("span", "alert-title", a.title));
      d.appendChild(sum);
      const body = el("div", "alert-body");
      body.appendChild(el("p", null, a.explanation && a.explanation.why ? a.explanation.why : a.detail));
      if (a.explanation && a.explanation.why && a.detail && a.explanation.why !== a.detail) body.appendChild(el("p", "muted", "Rule: " + a.detail));
      (a.evidence || []).forEach((e) => body.appendChild(kids(el("blockquote", null), el("p", null, e.text), el("cite", "mono", `${cap(e.drug)} label · ${SEC[e.section] || e.section.replace(/_/g, " ")}`))));
      body.appendChild(el("p", "mono muted", `Safety score ceiling: ${a.score_cap}/100`));
      d.appendChild(body);
      pills.appendChild(d);
    });
    box.appendChild(pills);
    return box;
  }

  function pairDetail(p) {
    const box = el("div", "pair-detail sev-" + p.severity);
    kids(box, kids(el("div", "pair-title"), el("h3", null, `${cap(p.a)} × ${cap(p.b)}`), badge(p.severity)));
    const x = p.explanation;
    kids(box, el("h4", null, "Why this happens, in simple words"), el("p", null, x.why));
    if (x.symptoms && x.symptoms.length) kids(box, el("h4", null, "Symptoms to watch for"), list(x.symptoms, "plain"));
    if (x.timing) kids(box, el("h4", null, "Timing and monitoring"), el("p", null, x.timing));
    if (p.label_hits.length) {
      box.appendChild(el("h4", null, "FDA label excerpts"));
      p.label_hits.slice(0, 4).forEach((h) => {
        const q = kids(el("blockquote", null), el("p", null, h.text),
          el("cite", "mono", `${cap(h.source)} label · ${SEC[h.section] || h.section} · ${h.match} mention of “${h.term}”`));
        box.appendChild(q);
      });
    }
    const f = p.faers;
    const fa = kids(el("div", "faers"), el("span", "eyebrow", "FAERS co-reports"),
      el("strong", "mono", num(f.total)), el("span", "muted", " reports list both drugs" + (f.top_reactions.length ? "; most common reactions: " + f.top_reactions.map((r) => `${r.term} (${num(r.count)})`).join(", ") : "")));
    box.appendChild(fa);
    if (x.questions && x.questions.length) kids(box, el("h4", null, "Ask your doctor or pharmacist"), list(x.questions, "plain"));
    return box;
  }

  function telemetry(r) {
    const d = el("details", "telemetry");
    d.appendChild(el("summary", null, "RxNorm & openFDA Telemetry Verification Audit"));
    d.appendChild(el("p", "muted", r.telemetry.note));
    const t = el("table", "tele");
    const h = t.createTHead().insertRow();
    ["Entered", "RxNorm name", "RxCUI", "Generic used for FDA search", "FDA sections found"].forEach((x) => h.appendChild(el("th", null, x)));
    const b = t.createTBody();
    r.drugs.forEach((dr) => {
      const row = b.insertRow();
      [dr.input, dr.rxnorm_name, dr.rxcui, dr.name, dr.sections_found.map((s) => SEC[s]).join(", ") || "none"].forEach((x, i) => row.insertCell().appendChild(el("span", i === 2 ? "mono" : null, x)));
    });
    d.appendChild(el("div", "scroll")).appendChild(t);
    const grp = (title, urls) => {
      d.appendChild(el("h4", null, title));
      const ul = el("ul", "urls");
      [...new Set(urls)].forEach((u) => { const li = el("li"); li.appendChild(link(u)); ul.appendChild(li); });
      d.appendChild(ul);
    };
    grp("RxNorm requests (rxnav.nlm.nih.gov)", r.telemetry.rxnorm_urls);
    grp("openFDA drug label queries (api.fda.gov/drug/label.json)", r.telemetry.label_urls);
    grp("openFDA FAERS co-report queries (api.fda.gov/drug/event.json)", r.telemetry.faers_urls);
    if (r.context_notes && r.context_notes.length) {
      d.appendChild(el("h4", null, "Patient-context label notes"));
      r.context_notes.forEach((n) => d.appendChild(kids(el("blockquote", null), el("p", null, n.text), el("cite", "mono", `${cap(n.drug)} · ${SEC[n.section] || n.section} · ${n.context}`))));
    }
    return d;
  }

  function monograph(host, r, state) {
    host.replaceChildren();
    host.appendChild(verdict(r));
    const al = alertsSection(r);
    if (al) host.appendChild(al);
    const grid = el("div", "viz-grid");
    const netBox = kids(el("div", "sheet viz"), el("div", "sheet-head", ""));
    netBox.firstChild.appendChild(el("h2", null, "Pairwise interaction network"));
    const netHost = el("div", "net");
    netBox.appendChild(netHost);
    const legend = el("div", "legend");
    [["major", "Major"], ["moderate", "Moderate"], ["minor", "Minor"], ["none", "Clear"]].forEach(([k, t]) => legend.appendChild(kids(el("span", "lg sev-" + k), el("i"), document.createTextNode(t))));
    netBox.appendChild(legend);
    const matBox = kids(el("div", "sheet viz"), kids(el("div", "sheet-head"), el("h2", null, "Severity matrix · Drug A × Drug B")));
    const matHost = el("div", "scroll");
    matBox.appendChild(matHost);
    kids(grid, netBox, matBox);
    host.appendChild(grid);
    const detail = el("div", "sheet detail");
    host.appendChild(detail);
    const first = Math.max(0, r.pairs.findIndex((p) => p.severity === r.overall.severity));
    state.selected = first;
    const paint = () => {
      Net.draw(netHost, r.drugs, r.pairs, state.selected, pick);
      Net.matrix(matHost, r.drugs, r.pairs, state.selected, pick);
      detail.replaceChildren(pairDetail(r.pairs[state.selected]));
    };
    const pick = (i) => { state.selected = i; paint(); };
    paint();
    if (r.drugs.some((d) => d.boxed_warning)) {
      const bw = kids(el("div", "sheet boxed"), el("h2", null, "FDA boxed warnings"));
      r.drugs.filter((d) => d.boxed_warning).forEach((d) => bw.appendChild(kids(el("blockquote", null), el("p", null, d.boxed_warning), el("cite", "mono", `${cap(d.name)} label · boxed warning (first 600 characters)`))));
      host.appendChild(bw);
    }
    if (r.questions.length) host.appendChild(kids(el("div", "sheet"), el("h2", null, "Questions to ask your doctor or pharmacist"), list(r.questions, "plain")));
    host.appendChild(telemetry(r));
    host.appendChild(el("p", "foot muted", `${r.source === "groq" ? "Plain-English explanations by Groq (" + r.model + ")" : "Explanations built directly from FDA label text (no AI rewrite)"} · ${r.disclaimer}`));
  }

  function alternative(host, r, state) {
    host.replaceChildren();
    const b = r.baseline.overall, p = r.proposed.overall;
    const cmp = kids(el("div", "sheet compare"), el("h2", null, "Safer Alternative Explorer"), el("p", "cmp-head", r.comparison.headline));
    const cols = el("div", "cmp-cols");
    const col = (title, meds, o) => kids(el("div", "cmp-col sev-" + o.severity), el("div", "eyebrow", title), el("div", "mono cmp-score", o.safety_score + " / 100"), badge(o.severity, o.severity_label), el("p", "muted", meds.map(cap).join(" + ")));
    kids(cols, col("Current regimen", r.baseline.medications, b), col("With the alternative", r.proposed.drugs.map((d) => d.name), p));
    kids(cmp, cols, el("p", "cmp-dir mono", `Safety score change: ${r.comparison.score_change > 0 ? "+" : ""}${r.comparison.score_change} (${r.comparison.direction})`));
    host.appendChild(cmp);
    const wrap = el("div");
    host.appendChild(wrap);
    monograph(wrap, r.proposed, state);
  }

  function sheet(host, r) {
    host.replaceChildren();
    const s = el("div", "sheet printable");
    kids(s, kids(el("div", "sheet-head"), el("h2", null, "Pharmacist Question Sheet")));
    if (!r) {
      s.appendChild(el("p", "muted", "Run a regimen audit first. Your personalised questions will appear here, ready to print and take to your pharmacist."));
      host.appendChild(s);
      return;
    }
    const a = r.kind === "alternative" ? r.proposed : r;
    kids(s, el("p", null, "Medicines: " + a.input.medications.map(cap).join(", ")), el("p", "muted", "Patient context: " + a.input.context.join(", ")),
      el("p", null, "Highest severity found: " + SEV[a.overall.severity]));
    s.appendChild(el("h3", null, "Questions"));
    s.appendChild(list(a.questions, "plain checklist"));
    s.appendChild(el("p", "foot muted", a.disclaimer));
    const pr = el("button", "btn", "Print this sheet");
    pr.addEventListener("click", () => window.print());
    s.appendChild(pr);
    host.appendChild(s);
  }
  return { monograph, alternative, sheet, el, kids, cap, SEV };
})();
