// node --test : static checks on the dashboard files and the headers that protect them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const FILES = ["../public/index.html", "../public/dashboard.js", "../public/dashboard.css"];

test("no markup sinks and no code from strings", () => {
  // Field values are untrusted: they may reach the page only as text.
  const banned = [/\binnerHTML\b/, /\bouterHTML\b/, /\binsertAdjacentHTML\b/, /\bdocument\.write(ln)?\b/,
    /\beval\s*\(/, /\bnew\s+Function\b/, /\bFunction\s*\(/, /\bsetTimeout\s*\(\s*["'`]/, /\bsetInterval\s*\(\s*["'`]/,
    /\bcreateContextualFragment\b/, /\bDOMParser\b/, /\bsrcdoc\b/, /javascript:/i];
  for (const f of FILES) {
    const src = read(f);
    for (const re of banned) assert.doesNotMatch(src, re, `${f} matches ${re}`);
  }
});

test("no external URL: no CDN, no web font, no third-party request", () => {
  for (const f of FILES) {
    assert.doesNotMatch(read(f), /https?:\/\//i, f);
    assert.doesNotMatch(read(f), /@import|url\(\s*["']?\/\//i, f);
  }
});

test("the CSP holds: no inline script, inline style or inline handler", () => {
  const html = read("../public/index.html");
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  assert.equal(scripts.length, 1);
  assert.match(scripts[0][1], /\bsrc="dashboard\.js"/);
  assert.equal(scripts[0][2].trim(), "");
  assert.doesNotMatch(html, /<style\b/i);
  assert.doesNotMatch(html, /\sstyle\s*=/i);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.match(html, /<link rel="stylesheet" href="dashboard\.css">/);
  // The JS sets no style attribute either (that is an inline style under the CSP).
  assert.doesNotMatch(read("../public/dashboard.js"), /setAttribute\(\s*["']style["']|\.style\b|\.cssText\b/);
});

test("the token stays in sessionStorage and never in the URL", () => {
  const js = read("../public/dashboard.js");
  assert.doesNotMatch(js, /localStorage|indexedDB|document\.cookie/);
  assert.match(js, /sessionStorage\.setItem/);
  assert.match(js, /Authorization: "Bearer " \+/);
  const fetches = [...js.matchAll(/fetch\(([^,]+),/g)].map((m) => m[1].trim());
  assert.deepEqual(fetches, ['"/api/stats?days=" + days']);
  assert.doesNotMatch(js, /location\.(href|search|hash)\s*=|history\.(push|replace)State/);
  const html = read("../public/index.html");
  assert.match(html, /<input id="token" type="password"/);
  // No password-manager save prompt for the stats token.
  assert.match(html, /<input id="token"[^>]*\bautocomplete="one-time-code"/);
  assert.match(html, /<button type="submit" id="show">/);
  assert.doesNotMatch(html, /<form[^>]*\b(action|method)=/i);
});

test("light and dark mode, and a narrow layout", () => {
  const css = read("../public/dashboard.css");
  assert.match(css, /@media \(prefers-color-scheme: dark\)/);
  assert.match(css, /@media \(max-width: 480px\)/);
  assert.match(read("../public/index.html"), /<meta name="viewport" content="width=device-width, initial-scale=1">/);
});

test("vercel.json keeps region and cron, and sends the security headers", () => {
  const v = JSON.parse(read("../vercel.json"));
  assert.deepEqual(v.regions, ["fra1"]);
  assert.deepEqual(v.crons, [{ path: "/api/prune", schedule: "0 3 * * *" }]);
  assert.equal(v.outputDirectory, "public");
  const all = v.headers.find((h) => h.source === "/(.*)");
  assert.ok(all, "headers for every path");
  const got = Object.fromEntries(all.headers.map((h) => [h.key, h.value]));
  assert.deepEqual(got, {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
  });
});

test("vercel.json caps every function at 10 seconds", () => {
  const v = JSON.parse(read("../vercel.json"));
  assert.deepEqual(v.functions, { "api/*.js": { maxDuration: 10 } });
});

test("the page has the write budget section, and saturated hours have their own style", () => {
  const html = read("../public/index.html");
  assert.match(html, /<h2>Write budget \(last 48 h\)<\/h2>/);
  assert.match(html, /<p id="budget-note"[^>]*><\/p>/);
  assert.match(html, /<div id="budget"><\/div>/);
  assert.match(read("../public/dashboard.css"), /\.saturated\b[^{]*\{/);
});
