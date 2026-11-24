/* Pure-SVG drug-interaction network and the clickable pairwise matrix. */
const Net = (() => {
  const COLORS = { major: "#BE123C", moderate: "#EA580C", minor: "#0369A1", none: "#0F766E" };
  const NS = "http://www.w3.org/2000/svg";
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const key = (a, b) => a + "|" + b;

  function pairMap(pairs) {
    const m = {};
    pairs.forEach((p, i) => { m[key(p.a, p.b)] = i; m[key(p.b, p.a)] = i; });
    return m;
  }

  function draw(host, drugs, pairs, selected, onPick) {
    const names = drugs.map((d) => d.name);
    const n = names.length, W = 440, H = 400, cx = W / 2, cy = H / 2, R = 140;
    const pos = names.map((_, i) => {
      const t = -Math.PI / 2 + (2 * Math.PI * i) / n;
      return { x: cx + R * Math.cos(t), y: cy + R * Math.sin(t) };
    });
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Drug interaction network");
    const add = (tag, attrs, parent = svg) => {
      const el = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
      parent.appendChild(el);
      return el;
    };
    const idx = (nm) => names.indexOf(nm);
    // draw clear/minor edges first so serious edges sit on top
    const order = pairs.map((p, i) => i).sort((x, y) => rank(pairs[x].severity) - rank(pairs[y].severity));
    order.forEach((i) => {
      const p = pairs[i], a = pos[idx(p.a)], b = pos[idx(p.b)];
      const on = i === selected;
      const g = add("g", { class: "edge" + (on ? " on" : ""), tabindex: 0, role: "button", "aria-label": `${p.a} and ${p.b}: ${p.severity_label}` });
      add("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: "transparent", "stroke-width": 16 }, g);
      add("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: COLORS[p.severity],
        "stroke-width": on ? 5 : p.severity === "none" ? 1.8 : 3.2,
        "stroke-dasharray": p.severity === "none" ? "5 5" : "none", "stroke-linecap": "round", opacity: on ? 1 : 0.85,
      }, g);
      g.addEventListener("click", () => onPick(i));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(i); } });
    });
    names.forEach((nm, i) => {
      const { x, y } = pos[i];
      const g = add("g", { class: "node" });
      add("circle", { cx: x, cy: y, r: 30, fill: "#fff", stroke: "#1D4ED8", "stroke-width": 2 }, g);
      add("circle", { cx: x, cy: y, r: 22, fill: "#EFF4FF" }, g);
      const t = add("text", { x, y: y + 4, "text-anchor": "middle", "font-size": 10.5, "font-weight": 600, fill: "#1E3A8A" }, g);
      t.textContent = nm.length > 11 ? nm.slice(0, 10) + "…" : cap(nm);
      const full = document.createElementNS(NS, "title");
      full.textContent = `${cap(nm)} · RxCUI ${drugs[i].rxcui}`;
      g.appendChild(full);
    });
    host.replaceChildren(svg);
  }

  function rank(s) { return { none: 0, minor: 1, moderate: 2, major: 3 }[s]; }

  function matrix(host, drugs, pairs, selected, onPick) {
    const names = drugs.map((d) => d.name), pm = pairMap(pairs);
    const table = document.createElement("table");
    table.className = "matrix";
    const head = table.createTHead().insertRow();
    head.appendChild(document.createElement("th"));
    names.forEach((n) => { const th = document.createElement("th"); th.textContent = cap(n); head.appendChild(th); });
    const body = table.createTBody();
    names.forEach((rn, r) => {
      const row = body.insertRow();
      const th = document.createElement("th"); th.textContent = cap(rn); row.appendChild(th);
      names.forEach((cn, c) => {
        const td = row.insertCell();
        if (r === c) { td.className = "diag"; td.textContent = "—"; return; }
        const i = pm[key(rn, cn)], p = pairs[i];
        td.className = "cell sev-" + p.severity + (i === selected ? " on" : "");
        td.tabIndex = 0;
        td.title = `${cap(rn)} × ${cap(cn)}: ${p.severity_label}`;
        td.textContent = { major: "Major", moderate: "Moderate", minor: "Minor", none: "Clear" }[p.severity];
        td.addEventListener("click", () => onPick(i));
        td.addEventListener("keydown", (e) => { if (e.key === "Enter") onPick(i); });
      });
    });
    host.replaceChildren(table);
  }
  return { draw, matrix, COLORS };
})();
