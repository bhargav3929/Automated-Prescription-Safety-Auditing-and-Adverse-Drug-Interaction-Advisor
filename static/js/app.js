/* UI wiring: chip composer, modes, samples, history, API keys. */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { meds: [], flags: new Set(), custom: false, mode: "audit", info: null, last: null, selected: 0, busy: false };
  let lookupSeq = 0;

  // ------------------------------------------------------------ helpers
  const show = (el, on) => el.classList.toggle("hidden", !on);
  function showError(e) {
    const box = $("error");
    box.replaceChildren();
    if (!e) return show(box, false);
    box.appendChild(document.createTextNode(e.error || String(e)));
    if (e.code === "AI_NOT_CONFIGURED" || e.code === "AI_KEY_INVALID") {
      if (state.info && state.info.mode === "local") {
        const b = document.createElement("button");
        b.className = "btn small"; b.textContent = "Open API Keys";
        b.addEventListener("click", openKeys);
        box.appendChild(b);
      }
    }
    show(box, true);
  }
  function setStatus(msg) { const s = $("status"); s.textContent = msg || ""; show(s, !!msg); }

  async function refreshInfo() {
    try {
      state.info = await Api.info();
    } catch (e) { return; }
    const i = state.info;
    const left = i.limits.runs_left;
    $("runs-pill").textContent = `${left}/${i.limits.hourly_runs} Free Demo Runs Left This Hour`;
    $("runs-pill").classList.toggle("low", left <= 3);
    show($("btn-keys"), i.mode === "local");
    if (i.mode === "demo" && i.demo && !i.demo.ticket_valid) {
      $("run-note").textContent = "Sign in on lastsem.com to try your own input. Samples work instantly.";
    }
  }

  // ------------------------------------------------------------ chips
  function renderChips() {
    const box = $("chips");
    box.replaceChildren();
    state.meds.forEach((m, i) => {
      const chip = document.createElement("span");
      chip.className = "chip " + (m.status || "pending");
      const name = document.createElement("b"); name.textContent = m.match ? m.match.generic : m.name;
      chip.appendChild(name);
      if (m.status === "ok") {
        const rx = document.createElement("span"); rx.className = "mono rx"; rx.textContent = "RxCUI " + m.match.rxcui; chip.appendChild(rx);
      } else if (m.status === "bad") {
        const rx = document.createElement("span"); rx.className = "rx bad"; rx.textContent = "not found in RxNorm"; chip.appendChild(rx);
      } else {
        const rx = document.createElement("span"); rx.className = "rx"; rx.textContent = "checking…"; chip.appendChild(rx);
      }
      const x = document.createElement("button"); x.className = "x"; x.setAttribute("aria-label", "Remove " + m.name); x.textContent = "×";
      x.addEventListener("click", () => { state.meds.splice(i, 1); renderChips(); });
      chip.appendChild(x);
      box.appendChild(chip);
    });
    const sel = $("alt-replace");
    const cur = sel.value;
    sel.replaceChildren();
    state.meds.forEach((m) => { const o = document.createElement("option"); o.value = m.name; o.textContent = m.match ? m.match.generic : m.name; sel.appendChild(o); });
    if (cur) sel.value = cur;
    $("run-note").textContent = state.meds.length < 2 ? "Add at least two medicines." : `${state.meds.length} medicines ready.`;
  }

  async function addMed(raw) {
    raw = raw.trim();
    if (!raw) return;
    const max = (state.info && state.info.max_medicines) || 8;
    if (state.meds.length >= max) return showError({ error: `You can audit up to ${max} medicines at a time.` });
    if (state.meds.some((m) => m.name.toLowerCase() === raw.toLowerCase())) return;
    showError(null);
    const m = { name: raw, status: "pending", match: null };
    state.meds.push(m);
    renderChips();
    try {
      const r = await Api.lookup(raw);
      m.match = r.match; m.status = r.match ? "ok" : "bad";
    } catch (e) { m.status = "pending"; }
    renderChips();
  }

  function medsFromText(text) {
    text.split(/[,;\n\r]+/).map((t) => t.trim()).filter(Boolean).forEach((t) => addMed(t));
  }

  // ------------------------------------------------------------ context
  function buildContext() {
    const wrap = $("context");
    (state.info ? state.info.context_options : []).forEach((o) => {
      const b = document.createElement("button");
      b.className = "ctx-btn" + (o.id === "standard" ? " on" : ""); b.dataset.id = o.id; b.textContent = o.label;
      b.addEventListener("click", () => {
        if (o.id === "standard") { state.flags.clear(); state.custom = false; }
        else if (state.flags.has(o.id)) state.flags.delete(o.id); else state.flags.add(o.id);
        syncContext();
      });
      wrap.appendChild(b);
    });
    const c = document.createElement("button");
    c.className = "ctx-btn"; c.dataset.id = "custom"; c.textContent = "Custom Patient Context...";
    c.addEventListener("click", () => { state.custom = !state.custom; syncContext(); });
    wrap.appendChild(c);
    syncContext();
  }
  function syncContext() {
    document.querySelectorAll(".ctx-btn").forEach((b) => {
      const id = b.dataset.id;
      b.classList.toggle("on", id === "standard" ? !state.flags.size && !state.custom : id === "custom" ? state.custom : state.flags.has(id));
    });
    show($("ctx-custom"), state.custom || state.flags.has("allergy"));
  }
  const context = () => ({ flags: [...state.flags], custom: state.custom || state.flags.has("allergy") ? $("ctx-custom").value : "" });
  function setContext(flags) {
    state.flags = new Set(flags || []); state.custom = false; syncContext();
  }

  // ------------------------------------------------------------ modes
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    show($("alt-box"), mode === "alt");
    show($("composer"), mode !== "sheet");
    show($("sheet-view"), mode === "sheet");
    show($("results"), mode !== "sheet" && !!state.last);
    $("composer-title").textContent = mode === "alt" ? "Pick a medicine to swap" : "Build the regimen";
    $("btn-run").textContent = mode === "alt" ? "Compare the alternative" : "Audit this regimen";
    if (mode === "sheet") Render.sheet($("sheet-view"), state.last);
  }

  // ------------------------------------------------------------ results
  function showResult(r) {
    state.last = r;
    showError(null);
    const host = $("results");
    if (r.kind === "alternative") Render.alternative(host, r, state);
    else if (r.kind === "rejection") {
      host.replaceChildren();
      const s = Render.el("div", "sheet rejection");
      Render.kids(s, Render.el("h2", null, "Request rejected — no advice generated"), Render.el("p", null, r.error),
        Render.el("p", "mono muted", `HTTP ${r.http_status} ${r.code} · hourly quota consumed: ${r.quota_consumed ? "yes" : "no"} · clinical advice returned: ${r.advice_returned ? "yes" : "no"}`));
      host.appendChild(s);
    } else Render.monograph(host, r, state);
    show(host, state.mode !== "sheet");
    if (state.mode === "sheet") Render.sheet($("sheet-view"), r);
    host.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function run() {
    if (state.busy) return;
    if (state.meds.length < 2) return showError({ error: "Enter at least two medicines to audit for interactions.", code: "BAD_INPUT" });
    state.busy = true; $("btn-run").disabled = true; showError(null);
    setStatus("Normalising names in RxNorm, reading FDA labels, then asking the Groq explainer… this can take 20–40 seconds.");
    const body = { medications: state.meds.map((m) => m.name), context: context() };
    try {
      let r;
      if (state.mode === "alt") {
        body.replace = $("alt-replace").value; body.alternative = $("alt-new").value;
        r = await Api.alternative(body);
      } else r = await Api.audit(body);
      showResult(r);
    } catch (e) { showError(e); }
    finally { state.busy = false; $("btn-run").disabled = false; setStatus(""); refreshInfo(); }
  }

  // ------------------------------------------------------------ samples
  async function loadSamples() {
    let data;
    try { data = await Api.samples(); } catch (e) { return; }
    const box = $("samples");
    data.samples.forEach((s) => {
      const b = document.createElement("button");
      b.className = "sample"; b.textContent = s.title;
      b.addEventListener("click", async () => {
        showError(null);
        try {
          const full = await Api.sample(s.id);
          state.meds = []; renderChips();
          medsFromText(full.input_text);
          setContext((full.context && full.context.flags) || []);
          if (state.mode === "sheet") setMode("audit");
          if (full.result) { showResult(full.result); }
          else setStatus("");
        } catch (e) { showError(e); }
      });
      box.appendChild(b);
    });
  }

  // ------------------------------------------------------------ history
  async function openHistory() {
    show($("drawer"), true);
    const list = $("history-list"); list.replaceChildren();
    let data;
    try { data = await Api.history(); } catch (e) { return; }
    if (!data.items.length) { const p = document.createElement("p"); p.className = "muted pad"; p.textContent = "No audits yet this session."; list.appendChild(p); return; }
    data.items.forEach((it) => {
      const b = document.createElement("button"); b.className = "hist";
      const t = document.createElement("b"); t.textContent = it.title;
      const m = document.createElement("span"); m.className = "muted mono"; m.textContent = `${it.kind} · ${new Date(it.ts * 1000).toLocaleString()}`;
      b.append(t, m);
      b.addEventListener("click", async () => { try { const f = await Api.historyItem(it.id); showResult(f.payload); show($("drawer"), false); } catch (e) { showError(e); } });
      list.appendChild(b);
    });
  }

  // ------------------------------------------------------------ keys
  async function openKeys() {
    show($("keys-modal"), true); $("keys-error").textContent = "";
    try {
      const k = await Api.keys();
      $("keys-status").textContent = k.groq.configured ? `Key in use: ${k.groq.masked} (from ${k.groq.source === "ui" ? "this screen" : ".env"})` : "No Groq key is set up yet.";
    } catch (e) { $("keys-status").textContent = e.error; }
  }
  async function saveKey() {
    try { await Api.saveKey($("keys-input").value.trim()); $("keys-input").value = ""; await openKeys(); await refreshInfo(); }
    catch (e) { $("keys-error").textContent = e.error; }
  }

  // ------------------------------------------------------------ boot
  async function boot() {
    await refreshInfo();
    buildContext();
    renderChips();
    loadSamples();
    $("btn-add").addEventListener("click", () => { addMed($("med-input").value); $("med-input").value = ""; $("med-input").focus(); });
    $("med-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addMed($("med-input").value); $("med-input").value = ""; }
    });
    let typing = null;
    $("med-input").addEventListener("input", () => {
      clearTimeout(typing);
      const q = $("med-input").value.trim();
      if (q.length < 3) return;
      typing = setTimeout(async () => {
        try {
          const r = await Api.suggest(q);
          if ($("med-input").value.trim() !== q) return;
          $("med-suggest").replaceChildren(...r.suggestions.map((x) => { const o = document.createElement("option"); o.value = x.name; o.label = "RxCUI " + x.rxcui; return o; }));
        } catch (e) { /* suggestions are optional */ }
      }, 300);
    });
    $("btn-paste-toggle").addEventListener("click", () => show($("paste-area"), $("paste-area").classList.contains("hidden")));
    $("btn-paste-apply").addEventListener("click", () => { medsFromText($("paste-text").value); $("paste-text").value = ""; show($("paste-area"), false); });
    $("btn-run").addEventListener("click", run);
    document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    $("btn-history").addEventListener("click", openHistory);
    $("btn-history-close").addEventListener("click", () => show($("drawer"), false));
    $("btn-history-clear").addEventListener("click", async () => { await Api.historyClear(); openHistory(); });
    $("btn-keys").addEventListener("click", openKeys);
    $("keys-close").addEventListener("click", () => show($("keys-modal"), false));
    $("keys-save").addEventListener("click", saveKey);
    $("keys-remove").addEventListener("click", async () => { try { await Api.removeKey(); await openKeys(); await refreshInfo(); } catch (e) { $("keys-error").textContent = e.error; } });
    setMode("audit");
  }
  boot();
})();
