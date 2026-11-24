/* Thin fetch wrapper. Every URL is relative so the app works under a sub-path. */
const Api = (() => {
  const TK = "ticket-store";
  let access = "";
  try {
    const url = new URL(window.location.href);
    const fromUrl = url.searchParams.get("ticket");
    if (fromUrl) {
      sessionStorage.setItem(TK, fromUrl);
      url.searchParams.delete("ticket");
      history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
    }
    access = sessionStorage.getItem(TK) || "";
  } catch (e) { /* storage blocked: samples still work */ }

  async function call(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (access) headers["X-LastSem-Ticket"] = access;
    let res;
    try {
      res = await fetch(path, Object.assign({}, opts, { headers }));
    } catch (e) {
      throw { error: "Couldn't reach the app. Check that it is still running and try again.", code: "NETWORK" };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (!res.ok) throw { status: res.status, error: (data && data.error) || "Something went wrong. Please try again.", code: (data && data.code) || "ERROR" };
    return data;
  }
  const post = (path, body) => call(path, { method: "POST", body: JSON.stringify(body) });
  return {
    info: () => call("api/info"),
    samples: () => call("api/samples"),
    sample: (id) => call("api/samples/" + encodeURIComponent(id)),
    lookup: (q) => call("api/drugs/lookup?q=" + encodeURIComponent(q)),
    suggest: (q) => call("api/drugs/suggest?q=" + encodeURIComponent(q)),
    audit: (body) => post("api/audit", body),
    alternative: (body) => post("api/check-alternative", body),
    history: () => call("api/history"),
    historyItem: (id) => call("api/history/" + id),
    historyClear: () => call("api/history", { method: "DELETE" }),
    keys: () => call("api/keys"),
    saveKey: (k) => post("api/keys", { groq_api_key: k }),
    removeKey: () => call("api/keys/groq", { method: "DELETE" }),
  };
})();
