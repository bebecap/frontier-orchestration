// node --test : runs the real dashboard.js in a small fake DOM, to check how it keeps the token.
// No browser and no network: fetch is a stub that the test answers by hand.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../public/dashboard.js", import.meta.url), "utf8");
const KEY = "fo.stats.token";

class FakeElement {
  constructor(doc, tag, id) {
    this.doc = doc;
    this.tagName = tag;
    this.id = id;
    this.children = [];
    this.listeners = {};
    this.attrs = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.namespaceURI = "urn:fake-svg";
    this.clientWidth = 600;
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  // A browser does not move the focus to a disabled control.
  focus() { if (!this.disabled) this.doc.activeElement = this; }
  fire(type) { for (const fn of this.listeners[type] || []) fn({ preventDefault() {} }); }
}

function makePage({ stored, timers } = {}) {
  const doc = { activeElement: null };
  const byId = {};
  const add = (tag, id, attrs = {}) => {
    const e = new FakeElement(doc, tag, id);
    Object.assign(e.attrs, attrs);
    if (id) byId[id] = e;
    return e;
  };
  for (const id of ["chart", "status", "auth", "controls", "results", "token", "show", "total", "window",
    "types", "events", "fields", "legend", "chartbox", "readout", "reload", "forget", "budget", "budget-note"]) add("div", id);
  byId.controls.hidden = true;
  byId.results.hidden = true;
  const segs = [7, 30, 90].map((d) => add("button", null, { "data-days": String(d) }));
  const lists = {
    "#controls button": [...segs, byId.reload, byId.forget],
    ".segmented button": segs,
  };
  doc.getElementById = (id) => byId[id] || null;
  doc.querySelectorAll = (sel) => {
    if (!(sel in lists)) throw new Error("fake DOM has no list for " + sel);
    return lists[sel];
  };
  doc.createElement = (tag) => add(tag);
  doc.createElementNS = (_ns, tag) => add(tag);
  doc.createTextNode = (text) => ({ textContent: String(text) });

  const store = new Map(stored === undefined ? [] : [[KEY, stored]]);
  const sessionStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
  };
  const requests = [];
  const fetch = (url, opts) => new Promise((resolve, reject) => requests.push({ url, opts, resolve, reject }));
  const window = { addEventListener() {} };
  const st = timers ? (fn, ms) => { timers.push({ fn, ms }); return timers.length; } : setTimeout;
  const ct = timers ? (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; } : clearTimeout;
  vm.runInNewContext(SOURCE, { document: doc, sessionStorage, fetch, window, setTimeout: st, clearTimeout: ct, console });
  return { doc, byId, store, requests };
}

const tick = () => new Promise((r) => setImmediate(r));
const answer = (status, body = { days: 30, events: 0, counts: {}, series: [], fields: [] }) =>
  ({ status, ok: status >= 200 && status < 300, json: async () => body });
const bearer = (req) => req.opts.headers.Authorization;

function typeAndSubmit(page, value) {
  page.byId.token.value = value;
  page.byId.auth.fire("submit");
}

test("the token form is disabled while a request runs", async () => {
  const page = makePage({ stored: "old-token" });
  assert.equal(page.requests.length, 1);
  assert.equal(page.byId.token.disabled, true, "token input disabled during the request");
  assert.equal(page.byId.show.disabled, true, "submit button disabled during the request");
  page.requests[0].resolve(answer(200));
  await tick();
  assert.equal(page.byId.token.disabled, false);
  assert.equal(page.byId.show.disabled, false);
});

test("a late 401 for the old token keeps a new token, and the new token is then used", async () => {
  const page = makePage({ stored: "old-token" });
  assert.equal(bearer(page.requests[0]), "Bearer old-token");
  // A new token arrives while the first request still runs.
  typeAndSubmit(page, "new-token");
  page.requests[0].resolve(answer(401));
  await tick();
  assert.equal(page.store.get(KEY), "new-token", "the 401 for the old token must not clear the new token");
  assert.equal(page.requests.length, 2, "the new token is sent once the old request ends");
  assert.equal(bearer(page.requests[1]), "Bearer new-token");
  page.requests[1].resolve(answer(200));
  await tick();
  assert.equal(page.store.get(KEY), "new-token");
});

test("a 401 for the current token clears it and asks again", async () => {
  const page = makePage({ stored: "old-token" });
  page.requests[0].resolve(answer(401));
  await tick();
  assert.equal(page.store.has(KEY), false);
  assert.equal(page.byId.auth.hidden, false);
  assert.match(page.byId.status.textContent, /Token rejected/);
  assert.equal(page.byId.token.disabled, false);
  assert.equal(page.doc.activeElement, page.byId.token, "the token input gets the focus");
});

test("a token with characters outside visible ASCII is not stored and not sent", async () => {
  for (const bad of ["tok en", "tök", "tok en", "tok​en", "tok\ten"]) {
    const page = makePage();
    typeAndSubmit(page, bad);
    await tick();
    assert.equal(page.store.has(KEY), false, JSON.stringify(bad) + " must not be stored");
    assert.equal(page.requests.length, 0, JSON.stringify(bad) + " must not be sent");
    assert.equal(page.byId.status.textContent, "Token has invalid characters");
    assert.equal(page.byId.status.className, "error");
    assert.equal(page.byId.token.value, "");
  }
});

test("a valid token is stored and sent", async () => {
  const page = makePage();
  typeAndSubmit(page, "  Abc-123_~!  ");
  assert.equal(page.store.get(KEY), "Abc-123_~!");
  assert.equal(page.requests.length, 1);
  assert.equal(bearer(page.requests[0]), "Bearer Abc-123_~!");
});

test("a stored token with invalid characters is dropped at start", async () => {
  const page = makePage({ stored: "tök" });
  assert.equal(page.requests.length, 0);
  assert.equal(page.store.has(KEY), false);
  assert.equal(page.byId.auth.hidden, false);
});

test("after a failed load the stored token can still be forgotten", async () => {
  for (const fail of [(r) => r.reject(new TypeError("network")), (r) => r.resolve(answer(503)),
    (r) => r.resolve({ status: 200, ok: true, json: async () => { throw new SyntaxError("x"); } })]) {
    const page = makePage({ stored: "kept-token" });
    fail(page.requests[0]);
    await tick();
    await tick();
    assert.equal(page.store.get(KEY), "kept-token");
    assert.equal(page.byId.controls.hidden, false, "Forget token must be reachable while a token is stored");
    page.byId.forget.fire("click");
    assert.equal(page.store.has(KEY), false);
  }
});

test("a body that never finishes still times out, and Forget token is then reachable", async () => {
  const timers = [];
  const page = makePage({ stored: "kept-token", timers });
  page.requests[0].resolve({ status: 200, ok: true, json: () => new Promise(() => {}) });  // body stalls
  await tick();
  const t = timers.find((x) => x.ms === 20000 && !x.cleared);
  assert.ok(t, "the timeout must still run while the body is read");
  t.fn();
  await tick();
  await tick();
  assert.equal(page.byId.controls.hidden, false);
  assert.equal(page.byId.forget.disabled, false, "Forget token must be enabled after the timeout");
  assert.match(page.byId.status.textContent, /complete JSON/);
});

// All text under a fake node, in document order.
const textOf = (n) => (n.textContent || "") + (n.children || []).map(textOf).join("");
const find = (n, pred, out = []) => {
  if (pred(n)) out.push(n);
  for (const c of n.children || []) find(c, pred, out);
  return out;
};
const withEvents = (budget) => ({ days: 30, events: 5, counts: { event: { route: 5 } },
  series: [{ day: "2026-11-15", counts: { route: 5 } }], fields: ["event"], budget });

test("the write budget lists each hour and marks saturated hours", async () => {
  const page = makePage({ stored: "tok" });
  page.requests[0].resolve(answer(200, withEvents({ per_hour: 100, hours: [
    { hour: "2026-11-15T10", n: 40, saturated: false },
    { hour: "2026-11-15T11", n: 100, saturated: true },
    { hour: "<b>x</b>", n: "7", saturated: "yes" },  // untrusted: text only, not saturated
  ] })));
  await tick();
  const rows = find(page.byId.budget, (n) => n.tagName === "tr" && n.className !== "head");
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r.className), ["", "saturated", ""]);
  assert.match(textOf(rows[0]), /2026-11-15 10:00/);
  assert.match(textOf(rows[0]), /40/);
  assert.match(textOf(rows[0]), /40%/);
  assert.match(textOf(rows[1]), /100/);
  assert.match(textOf(rows[1]), /saturated/);
  assert.doesNotMatch(textOf(rows[0]), /saturated/);
  assert.match(textOf(rows[2]), /<b>x<\/b>/, "a bad hour is shown as plain text");
  assert.equal(find(page.byId.budget, (n) => n.tagName === "b").length, 0);
  assert.equal(page.byId["budget-note"].textContent, "Limit: 100 events per hour. 1 of 3 hours saturated.");
});

test("no budget rows: the section says so", async () => {
  for (const budget of [{ per_hour: 2000, hours: [] }, undefined]) {
    const page = makePage({ stored: "tok" });
    page.requests[0].resolve(answer(200, withEvents(budget)));
    await tick();
    assert.equal(find(page.byId.budget, (n) => n.tagName === "tr").length, 0);
    assert.match(page.byId["budget-note"].textContent, /No writes in the last 48 hours/);
  }
});
