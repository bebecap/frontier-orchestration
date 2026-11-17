import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { buildBundle, checkVersions, ITEMS } from '../scripts/bundle.mjs';
import { REPO_ROOT, snapshot, tmpdir, VERSION } from './helpers.mjs';

/** A fake repository root and npm folder with the four version sources. */
function fakeRoot({ pkg = '1.2.3', manifest = '1.2.3', marketplace = ['1.2.3'] } = {}) {
  const root = tmpdir('fo-npx-ver-');
  const write = (rel, obj) => {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), JSON.stringify(obj));
  };
  for (const m of ['.claude-plugin/plugin.json', '.codex-plugin/plugin.json', '.antigravity-plugin/plugin.json']) write(m, { version: manifest });
  write('.claude-plugin/marketplace.json', { plugins: marketplace.map((v) => (v === undefined ? { name: 'x' } : { name: 'x', version: v })) });
  write('npm/package.json', { version: pkg });
  return { root, npmDir: path.join(root, 'npm') };
}

test('checkVersions also checks the version of each plugin in the marketplace file', () => {
  let f = fakeRoot();
  assert.equal(checkVersions(f.root, f.npmDir), '1.2.3');
  f = fakeRoot({ marketplace: ['1.2.3', '1.2.2'] });
  assert.throws(() => checkVersions(f.root, f.npmDir), /\.claude-plugin\/marketplace\.json plugins\[1\]\.version \(1\.2\.2\)/);
  f = fakeRoot({ marketplace: [undefined] });
  assert.throws(() => checkVersions(f.root, f.npmDir), /marketplace\.json plugins\[0\]\.version \(undefined\)/);
  f = fakeRoot({ marketplace: [] });
  assert.throws(() => checkVersions(f.root, f.npmDir), /marketplace\.json lists no plugin/);
  // The repository itself agrees.
  assert.equal(checkVersions(REPO_ROOT), VERSION);
});

test('buildBundle puts the old bundle back when the final rename fails', (t) => {
  const out = path.join(tmpdir(), 'bundle');
  buildBundle(REPO_ROOT, out);
  fs.writeFileSync(path.join(out, 'old-bundle-marker'), 'old');
  const realRename = fs.renameSync;
  t.mock.method(fs, 'renameSync', (from, to) => {
    if (to === out && path.basename(from).startsWith('.bundle-tmp-') && !from.endsWith('.old')) {
      throw Object.assign(new Error('rename failed (simulated)'), { code: 'EIO' });
    }
    return realRename(from, to);
  });
  assert.throws(() => buildBundle(REPO_ROOT, out), /rename failed \(simulated\)/);
  t.mock.restoreAll();
  assert.equal(fs.readFileSync(path.join(out, 'old-bundle-marker'), 'utf8'), 'old', 'the old bundle is gone');
  assert.deepEqual(fs.readdirSync(path.dirname(out)), ['bundle'], 'temporary or set-aside folders left behind');
});

// ---- The output folder: the script never deletes or changes the source ----

/**
 * A copy of the repository (the bundle sources, npm/package.json and npm/scripts/bundle.mjs) in a
 * temporary folder, one level below a wrapper folder. The script runs from that copy, so a wrong
 * --out can only damage the copy, never this checkout.
 */
function fakeCheckout() {
  const wrap = tmpdir('fo-npx-out-');
  const root = path.join(wrap, 'repo');
  for (const item of ITEMS) fs.cpSync(path.join(REPO_ROOT, item), path.join(root, item), { recursive: true });
  fs.mkdirSync(path.join(root, 'npm', 'scripts'), { recursive: true });
  fs.copyFileSync(path.join(REPO_ROOT, 'npm', 'package.json'), path.join(root, 'npm', 'package.json'));
  fs.copyFileSync(path.join(REPO_ROOT, 'npm', 'scripts', 'bundle.mjs'), path.join(root, 'npm', 'scripts', 'bundle.mjs'));
  return { wrap, root, npm: path.join(root, 'npm') };
}

// The name of the file that marks a folder written by scripts/bundle.mjs.
const BUNDLE_MARKER = '.fo-bundle';

function runBundle(f, args, cwd = f.npm) {
  return spawnSync(process.execPath, [path.join(f.npm, 'scripts', 'bundle.mjs'), ...args], { cwd, encoding: 'utf8', timeout: 60000 });
}

for (const [name, out, cwd] of [
  ['--out . (the npm folder)', () => '.', (f) => f.npm],
  ['--out .. (the repository root)', () => '..', (f) => f.npm],
  ['--out <repository root>', (f) => f.root, (f) => f.wrap],
  ['--out <a parent of the repository root>', (f) => f.wrap, (f) => f.wrap],
  ['--out <a source folder>', (f) => path.join(f.root, 'skills'), (f) => f.wrap],
  ['--out <a path inside a source folder>', (f) => path.join(f.root, 'skills', 'frontier-orchestration', 'out'), (f) => f.wrap],
  ['--out <a symbolic link to the repository root>', (f) => {
    fs.symlinkSync(f.root, path.join(f.wrap, 'link'));
    return path.join(f.wrap, 'link');
  }, (f) => f.wrap],
]) {
  test(`bundle refuses ${name}: exit 2, nothing changed`, () => {
    const f = fakeCheckout();
    const o = out(f);
    const before = snapshot(f.wrap);
    const r = runBundle(f, ['--out', o], cwd(f));
    assert.equal(r.status, 2, r.stdout + r.stderr);
    assert.match(r.stderr, /refusing --out/);
    assert.deepEqual(snapshot(f.wrap), before, 'the script changed the checkout');
  });
}

test('bundle refuses an existing folder that it did not write: exit 2, the folder stays', () => {
  const f = fakeCheckout();
  const foreign = path.join(f.wrap, 'mine');
  fs.mkdirSync(foreign);
  fs.writeFileSync(path.join(foreign, 'notes.txt'), 'keep me');
  const before = snapshot(f.wrap);
  const r = runBundle(f, ['--out', foreign]);
  assert.equal(r.status, 2, r.stdout + r.stderr);
  assert.match(r.stderr, /this script did not write it/);
  assert.deepEqual(snapshot(f.wrap), before);
  assert.equal(fs.readFileSync(path.join(foreign, 'notes.txt'), 'utf8'), 'keep me');
});

test('bundle writes a new folder with its marker, then replaces its own folder', () => {
  const f = fakeCheckout();
  const out = path.join(f.wrap, 'out', 'bundle');
  let r = runBundle(f, ['--out', out]);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.ok(fs.existsSync(path.join(out, BUNDLE_MARKER)), 'no marker in the bundle');
  assert.ok(fs.existsSync(path.join(out, 'skills', 'frontier-orchestration', 'SKILL.md')));
  fs.writeFileSync(path.join(out, 'stale.txt'), 'from the earlier build');
  r = runBundle(f, ['--out', out]);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.ok(!fs.existsSync(path.join(out, 'stale.txt')), 'the earlier bundle was not replaced');
  // The default output (npm/bundle) also works, twice.
  for (let i = 0; i < 2; i++) assert.equal(runBundle(f, []).status, 0);
  assert.ok(fs.existsSync(path.join(f.npm, 'bundle', BUNDLE_MARKER)));
  assert.ok(fs.existsSync(path.join(f.npm, 'scripts', 'bundle.mjs')), 'the script deleted itself');
});
