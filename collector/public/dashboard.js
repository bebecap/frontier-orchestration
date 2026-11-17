// Telemetry dashboard for the maintainer. Reads /api/stats with the stats token.
// Every field name and value from the server is untrusted: it goes into the page only as
// textContent or an SVG attribute, never as markup.
"use strict";

(function () {
  const KEY = "fo.stats.token";  // sessionStorage only (this tab), never persistent storage, never the URL
  // Fixed event-to-color slots, so a color always means the same event in every window.
  // Events not named here share the neutral "other" color.
  const SLOTS = ["route", "ask", "setup", "doctor", "lessons", "refresh", "jev_ping"];
  const OTHER = "other";

  const $ = (id) => document.getElementById(id);
  const chart = $("chart");
  const SVG_NS = chart.namespaceURI;  // taken from the page, so no namespace URL in this file
  const fmt = new Intl.NumberFormat("en");
  let days = 30, last = null, busy = false;

  function readToken() { try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; } }
  function saveToken(t) { try { sessionStorage.setItem(KEY, t); } catch { /* private mode: keep it in memory only */ } }
  function dropToken() { try { sessionStorage.removeItem(KEY); } catch { /* nothing stored */ } }
  // Visible ASCII only: other characters either make fetch throw (non-Latin-1) or reach the
  // server changed, and a real token never has them.
  function validToken(t) { return /^[\x21-\x7e]+$/.test(t); }
  let memoryToken = readToken();
  if (memoryToken && !validToken(memoryToken)) { memoryToken = ""; dropToken(); }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = String(text);
    return e;
  }
  function svg(tag, attrs) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k in attrs) e.setAttribute(k, String(attrs[k]));
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function status(text, isError) {
    const s = $("status");
    s.textContent = text;
    s.className = isError ? "error" : "";
  }
  function slotClass(name) {
    const i = SLOTS.indexOf(name);
    return i < 0 ? "c0" : "c" + (i + 1);
  }
  function groupOf(name) { return SLOTS.includes(name) ? name : OTHER; }
  // Own enumerable entries of a plain JSON object, as [string, count] sorted by count.
  function entries(obj) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return [];
    return Object.keys(obj)
      .map((k) => [k, Number(obj[k])])
      .filter((e) => Number.isFinite(e[1]) && e[1] > 0)
      .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  }
  function pct(n, total) {
    if (!total) return "0%";
    const p = (100 * n) / total;
    return (p >= 10 || p === 0 ? p.toFixed(0) : p.toFixed(1)) + "%";
  }

  function showAuth(message, isError) {
    $("auth").hidden = false;
    $("controls").hidden = true;
    $("results").hidden = true;
    status(message || "", isError);
    $("token").focus();
  }

  // While a request runs, no control can start another one or change the token.
  function setBusy(on) {
    busy = on;
    for (const b of document.querySelectorAll("#controls button")) b.disabled = on;
    $("token").disabled = on;
    $("show").disabled = on;
  }

  async function load() {
    if (busy || !memoryToken) return;
    const used = memoryToken;  // the token this request sends
    setBusy(true);
    let rejected = false;
    try {
      rejected = await request(used);
    } finally {
      setBusy(false);
    }
    if (rejected && memoryToken === used) {
      // Clear the token only if it is still the one the server rejected.
      memoryToken = "";
      dropToken();
      showAuth("Token rejected. Enter the stats token again.", true);
      return;
    }
    // The token changed while the request ran: load again with the new one.
    if (memoryToken && memoryToken !== used) load();
  }

  // A failed load keeps the token, so Reload and Forget token must stay reachable.
  function failed(message) {
    status(message, true);
    if (memoryToken) $("controls").hidden = false;
    return false;
  }

  // One GET of /api/stats with the given token. Returns true only for a 401.
  async function request(token) {
    status("Loading " + days + " days...");
    // A hung request (headers or body) would keep the form disabled: give up after 20 s.
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    let timer = null;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => { if (ctl) ctl.abort(); reject(new Error("timeout")); }, 20000);
    });
    deadline.catch(() => {});  // handled by the races below
    try {
      let res;
      try {
        res = await Promise.race([fetch("/api/stats?days=" + days, {
          headers: { Authorization: "Bearer " + token },
          cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
          signal: ctl ? ctl.signal : undefined,
        }), deadline]);
      } catch {
        return failed("Could not reach the collector. Check the network and try again.");
      }
      if (res.status === 401) return true;
      if (!res.ok) return failed("Could not load stats (HTTP " + res.status + "). Try again later.");
      let data;
      try { data = await Promise.race([res.json(), deadline]); }
      catch { return failed("The collector did not send a complete JSON answer."); }
      $("auth").hidden = true;
      $("controls").hidden = false;
      last = data;
      render(data);
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  function render(data) {
    const counts = data && typeof data.counts === "object" && data.counts ? data.counts : {};
    const total = Number(data && data.events) || 0;
    const windowDays = Number(data && data.days) || days;
    if (total <= 0) {
      $("results").hidden = true;
      status("No events in the last " + windowDays + " days.");
      return;
    }
    status("");
    $("results").hidden = false;
    const evs = entries(counts.event);
    $("total").textContent = fmt.format(total);
    $("window").textContent = windowDays + (windowDays === 1 ? " day" : " days");
    $("types").textContent = fmt.format(evs.length);

    renderTable($("events"), null, evs, true);
    renderChart(Array.isArray(data.series) ? data.series : [], windowDays);
    renderBudget(data.budget);

    const box = $("fields");
    clear(box);
    const names = Array.isArray(data.fields) ? data.fields.map(String) : Object.keys(counts);
    for (const name of names.filter((n) => n !== "event").sort()) {
      const card = el("section", "card");
      renderTable(card, name, entries(counts[name]), false);
      box.appendChild(card);
    }
  }

  function renderTable(host, caption, rows, colored) {
    clear(host);
    const sum = rows.reduce((a, r) => a + r[1], 0);
    const table = el("table");
    if (caption !== null) table.appendChild(el("caption", null, caption));
    const head = el("tr");
    head.appendChild(el("th", null, "Value"));
    head.appendChild(el("th", "num", "Count"));
    head.appendChild(el("th", null, "Share"));
    const thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    const body = el("tbody");
    for (const [value, n] of rows) {
      const tr = el("tr");
      const td = el("td", "value");
      if (colored) td.appendChild(swatch(slotClass(value)));
      td.appendChild(document.createTextNode(value));
      tr.appendChild(td);
      tr.appendChild(el("td", "num", fmt.format(n)));
      const share = el("td", "share");
      const bar = svg("svg", { viewBox: "0 0 100 8", preserveAspectRatio: "none", "aria-hidden": "true" });
      bar.appendChild(svg("rect", { class: "track", x: 0, y: 0, width: 100, height: 8, rx: 2 }));
      bar.appendChild(svg("rect", { class: colored ? slotClass(value) : "bar", x: 0, y: 0,
        width: sum ? Math.max((100 * n) / sum, 1) : 0, height: 8, rx: 2 }));
      share.appendChild(bar);
      share.appendChild(el("span", null, pct(n, sum)));
      tr.appendChild(share);
      body.appendChild(tr);
    }
    table.appendChild(body);
    host.appendChild(table);
  }

  // Admitted events per UTC hour against the hourly limit, oldest first, as the server sends them.
  function renderBudget(budget) {
    const host = $("budget");
    clear(host);
    const perHour = Number(budget && budget.per_hour);
    const limit = Number.isFinite(perHour) && perHour > 0 ? "Limit: " + fmt.format(perHour) + " events per hour." : "";
    const hours = budget && Array.isArray(budget.hours) ? budget.hours.filter((h) => h && typeof h === "object") : [];
    if (!hours.length) {
      $("budget-note").textContent = "No writes in the last 48 hours." + (limit ? " " + limit : "");
      return;
    }
    const full = hours.filter((h) => h.saturated === true).length;
    $("budget-note").textContent = (limit ? limit + " " : "") + full + " of " + hours.length +
      (hours.length === 1 ? " hour" : " hours") + " saturated.";
    const table = el("table");
    const head = el("tr", "head");
    head.appendChild(el("th", null, "Hour (UTC)"));
    head.appendChild(el("th", "num", "Events"));
    head.appendChild(el("th", "num", "Use"));
    head.appendChild(el("th", null, ""));
    const thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    const body = el("tbody");
    for (const h of hours) {
      const saturated = h.saturated === true;
      const n = Number(h.n);
      const count = Number.isFinite(n) && n >= 0 ? n : 0;
      const hour = String(h.hour);
      const tr = el("tr", saturated ? "saturated" : "");
      tr.appendChild(el("td", "value", /^\d{4}-\d{2}-\d{2}T\d{2}$/.test(hour) ? hour.replace("T", " ") + ":00" : hour));
      tr.appendChild(el("td", "num", fmt.format(count)));
      tr.appendChild(el("td", "num", perHour > 0 ? Math.round((100 * count) / perHour) + "%" : ""));
      const flag = el("td");
      if (saturated) flag.appendChild(el("span", "flag", "saturated"));
      tr.appendChild(flag);
      body.appendChild(tr);
    }
    table.appendChild(body);
    host.appendChild(table);
  }

  function swatch(cls) {
    const s = svg("svg", { width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": "true" });
    s.appendChild(svg("rect", { class: cls, x: 0, y: 0, width: 10, height: 10, rx: 2 }));
    const wrap = el("span");
    wrap.appendChild(s);
    wrap.appendChild(document.createTextNode(" "));
    return wrap;
  }

  // The UTC days of the window, oldest first, plus any day the server sent outside it.
  function dayList(series, n) {
    const out = [];
    const now = new Date();
    const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    for (let i = n - 1; i >= 0; i--) out.push(new Date(today - i * 86400000).toISOString().slice(0, 10));
    const seen = new Set(out);
    for (const p of series) if (p && typeof p.day === "string" && !seen.has(p.day)) { seen.add(p.day); out.push(p.day); }
    return out.sort();
  }

  function niceMax(v) {
    if (v <= 4) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function renderChart(series, n) {
    const byDay = new Map();
    for (const p of series) if (p && typeof p.day === "string") byDay.set(p.day, p.counts || {});
    const daysShown = dayList(series, n);
    // Stack groups in slot order, then "other", so a color keeps its place in every bar.
    const present = new Set();
    const stacks = daysShown.map((d) => {
      const g = {};
      for (const [name, c] of entries(byDay.get(d))) {
        const k = groupOf(name);
        g[k] = (g[k] || 0) + c;
        present.add(k);
      }
      return g;
    });
    const order = SLOTS.concat([OTHER]).filter((k) => present.has(k));

    const legend = $("legend");
    clear(legend);
    for (const k of order) {
      const li = el("li");
      const s = svg("svg", { width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": "true" });
      s.appendChild(svg("rect", { class: k === OTHER ? "c0" : slotClass(k), x: 0, y: 0, width: 10, height: 10, rx: 2 }));
      li.appendChild(s);
      li.appendChild(document.createTextNode(k === OTHER ? "other events" : k));
      legend.appendChild(li);
    }

    clear(chart);
    const W = Math.max(Math.round($("chartbox").clientWidth || 600), 280), H = 220;
    const pad = { l: 36, r: 6, t: 8, b: 24 };
    chart.setAttribute("viewBox", "0 0 " + W + " " + H);
    const totals = stacks.map((g) => Object.values(g).reduce((a, b) => a + b, 0));
    const top = niceMax(Math.max(1, ...totals));
    const plotH = H - pad.t - pad.b, plotW = W - pad.l - pad.r;
    const y = (v) => pad.t + plotH - (v / top) * plotH;

    for (const t of [0, top / 2, top]) {
      const yy = Math.round(y(t)) + 0.5;
      chart.appendChild(svg("line", { class: t === 0 ? "baseline" : "grid", x1: pad.l, x2: W - pad.r, y1: yy, y2: yy }));
      const label = svg("text", { class: "tick", x: pad.l - 6, y: yy + 4, "text-anchor": "end" });
      label.textContent = fmt.format(t);
      chart.appendChild(label);
    }

    const band = plotW / daysShown.length;
    const barW = Math.max(1, Math.min(28, band * 0.7));
    const GAP = 2;  // surface gap between stacked segments
    const labelEvery = Math.ceil(daysShown.length / Math.max(1, Math.floor(plotW / 64)));
    daysShown.forEach((d, i) => {
      const x = pad.l + i * band + (band - barW) / 2;
      let base = 0;
      for (const k of order) {
        const v = stacks[i][k] || 0;
        if (!v) continue;
        const y0 = y(base), y1 = y(base + v);
        const h = y0 - y1 - (base > 0 ? GAP : 0);
        if (h > 0) chart.appendChild(svg("rect", { class: k === OTHER ? "c0" : slotClass(k), x, y: y1, width: barW, height: h }));
        base += v;
      }
      // Label every few days, and always the last day. The last label ends at the right edge, so
      // it is not cut; a regular label too near it is left out, so the two do not overlap.
      const lastDay = i === daysShown.length - 1;
      const cx = pad.l + i * band + band / 2;
      if (lastDay || (i % labelEvery === 0 && cx < W - pad.r - 64)) {
        const t = svg("text", { class: "tick", x: lastDay ? W - pad.r : cx, y: H - 6,
          "text-anchor": lastDay ? "end" : "middle" });
        t.textContent = d.slice(5);
        chart.appendChild(t);
      }
      addHit(d, i);
    });

    function describe(i) {
      const d = daysShown[i];
      const parts = entries(byDay.get(d)).map((e) => e[0] + " " + fmt.format(e[1]));
      return d + ": " + fmt.format(totals[i]) + (parts.length ? " (" + parts.join(", ") + ")" : " events");
    }
    function addHit(d, i) {
      const hit = svg("rect", { class: "hit", x: pad.l + i * band, y: pad.t, width: band, height: plotH });
      const title = svg("title", {});
      title.textContent = describe(i);
      hit.appendChild(title);
      hit.addEventListener("pointerenter", () => { $("readout").textContent = describe(i); });
      chart.appendChild(hit);
    }
    $("readout").textContent = "";
  }

  $("auth").addEventListener("submit", (e) => {
    e.preventDefault();  // never a form submit: that could put the token in a URL
    const t = $("token").value.trim();
    $("token").value = "";
    if (!t) return;
    if (!validToken(t)) { status("Token has invalid characters", true); return; }  // not stored, not sent
    memoryToken = t;
    saveToken(t);
    load();
  });
  for (const b of document.querySelectorAll(".segmented button")) {
    b.addEventListener("click", () => {
      days = Number(b.getAttribute("data-days")) || 30;
      for (const o of document.querySelectorAll(".segmented button")) o.setAttribute("aria-pressed", String(o === b));
      load();
    });
  }
  $("reload").addEventListener("click", load);
  $("forget").addEventListener("click", () => {
    memoryToken = "";
    last = null;
    dropToken();
    showAuth("Token forgotten.");
  });
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (last && !$("results").hidden) renderChart(last.series || [], Number(last.days) || days); }, 150);
  });

  if (memoryToken) load(); else showAuth("");
})();
