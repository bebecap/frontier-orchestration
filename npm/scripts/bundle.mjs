#!/usr/bin/env node
// Copies the plugin layout from the repository root into npm/bundle/.
// `npm pack` runs this script as its prepack step. The bundle is a build output (git-ignored).
//
// Usage: node scripts/bundle.mjs [--out <folder>]
//
// Exit codes: 0 the bundle is written; 1 the build failed; 2 the output folder is refused
// (nothing was changed). See checkOut() for the refusals.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const NPM_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const REPO_ROOT = path.resolve(NPM_DIR, '..');
export const DEFAULT_OUT = path.join(NPM_DIR, 'bundle');

// What the hosts need: the manifests, the skill, the setup command, the logo, and the legal files.
export const ITEMS = [
  '.claude-plugin',
  '.codex-plugin',
  '.agents',
  '.antigravity-plugin',
  'skills/frontier-orchestration',
  'commands',
  'assets',
  'LICENSE',
  'PRIVACY.md',
];

// A file in each bundle that says this script wrote it. The script replaces an existing output
// folder only when the folder holds this file.
export const BUNDLE_MARKER = '.fo-bundle';

/** The output folder is refused. Nothing was changed. The command line exits 2. */
export class RefusedOutput extends Error {}

// Names that never go into the bundle, at any depth.
const SKIP_NAMES = new Set(['__pycache__', 'tests', 'node_modules', '.git', '.DS_Store']);
const SKIP_SUFFIXES = ['.pyc'];

function skipped(name) {
  return SKIP_NAMES.has(name) || SKIP_SUFFIXES.some((s) => name.endsWith(s));
}

function copyTree(src, dst) {
  const st = fs.lstatSync(src);
  if (st.isSymbolicLink()) throw new Error(`refusing to bundle a symbolic link: ${src}`);
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const name of fs.readdirSync(src).sort()) {
      if (!skipped(name)) copyTree(path.join(src, name), path.join(dst, name));
    }
  } else if (st.isFile()) {
    fs.copyFileSync(src, dst);
    fs.chmodSync(dst, st.mode & 0o777);
  } else {
    throw new Error(`refusing to bundle a special file: ${src}`);
  }
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** The package version must equal every plugin manifest version: the install folder is named by it. */
export function checkVersions(root, npmDir = NPM_DIR) {
  const want = readJson(path.join(npmDir, 'package.json')).version;
  const found = {
    '.claude-plugin/plugin.json': readJson(path.join(root, '.claude-plugin/plugin.json')).version,
    '.codex-plugin/plugin.json': readJson(path.join(root, '.codex-plugin/plugin.json')).version,
    '.antigravity-plugin/plugin.json': readJson(path.join(root, '.antigravity-plugin/plugin.json')).version,
  };
  // The Claude Code marketplace file also gives a version for each plugin it lists.
  const market = '.claude-plugin/marketplace.json';
  const plugins = readJson(path.join(root, market)).plugins;
  if (!Array.isArray(plugins) || !plugins.length) throw new Error(`${market} lists no plugin`);
  plugins.forEach((pl, i) => {
    found[`${market} plugins[${i}].version`] = pl && pl.version;
  });
  const bad = Object.entries(found).filter(([, v]) => v !== want);
  if (bad.length) {
    throw new Error(`npm/package.json version ${want} does not match ${bad.map(([f, v]) => `${f} (${v})`).join(', ')}`);
  }
  return want;
}

/** True when target is the folder base or a path inside it. */
function inside(target, base) {
  const rel = path.relative(base, target);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

/**
 * The real path of p, also when p (or some of its parents) does not exist yet: the real path of
 * the nearest parent that exists, then the rest of the names. Symbolic links in the parents are
 * thus resolved, so "--out link-to-the-checkout/x" is seen as a path in the checkout.
 */
function realPathOf(p) {
  const rest = [];
  let cur = path.resolve(p);
  for (;;) {
    try {
      return path.join(fs.realpathSync(cur), ...rest);
    } catch (e) {
      if (e.code !== 'ENOENT' && e.code !== 'ENOTDIR') throw e;
    }
    const up = path.dirname(cur);
    if (up === cur) return path.join(cur, ...rest);
    rest.unshift(path.basename(cur));
    cur = up;
  }
}

/**
 * Refuses an output folder that would destroy or change the source (RefusedOutput). The output
 * is refused when it is:
 * - the repository root, the npm folder, or a parent of one of them;
 * - a source item of the bundle, a parent of one, or a path inside one;
 * - a symbolic link;
 * - a path that exists and is not a folder that this script wrote (no BUNDLE_MARKER in it).
 * Returns the real path of the output folder.
 */
export function checkOut(root, out, npmDir = NPM_DIR) {
  const realOut = realPathOf(out);
  const protect = [['the repository root', realPathOf(root)], ['the npm folder', realPathOf(npmDir)]];
  for (const [what, p] of protect) {
    if (inside(p, realOut)) throw new RefusedOutput(`refusing --out ${out}: it is ${what} or a parent of it (${p})`);
  }
  for (const item of ITEMS) {
    const src = realPathOf(path.join(root, item));
    if (inside(src, realOut) || inside(realOut, src)) {
      throw new RefusedOutput(`refusing --out ${out}: it holds, is, or is inside the source ${item} (${src})`);
    }
  }
  let st = null;
  try {
    st = fs.lstatSync(out);
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }
  if (st && st.isSymbolicLink()) throw new RefusedOutput(`refusing --out ${out}: it is a symbolic link`);
  if (st && !st.isDirectory()) throw new RefusedOutput(`refusing --out ${out}: it exists and is not a folder`);
  if (st && !fs.existsSync(path.join(out, BUNDLE_MARKER))) {
    throw new RefusedOutput(`refusing --out ${out}: the folder exists and this script did not write it (no ${BUNDLE_MARKER} file). Delete it or use another folder.`);
  }
  return realOut;
}

function lexists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

/** True when dir is a real folder (not a symbolic link) that holds BUNDLE_MARKER as a file. */
function ownBundle(dir) {
  try {
    return fs.lstatSync(dir).isDirectory() && fs.lstatSync(path.join(dir, BUNDLE_MARKER)).isFile();
  } catch {
    return false;
  }
}

/**
 * Builds the bundle in a temporary sibling folder, then puts it in place with a rename.
 * A reader never sees a half-copied bundle. It first checks the output folder (checkOut) and the
 * versions; a refusal changes nothing.
 */
export function buildBundle(root = REPO_ROOT, out = DEFAULT_OUT, npmDir = NPM_DIR) {
  out = checkOut(root, out, npmDir);
  const version = checkVersions(root, npmDir);
  const parent = path.dirname(out);
  fs.mkdirSync(parent, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(parent, '.bundle-tmp-'));
  try {
    for (const item of ITEMS) {
      const src = path.join(root, item);
      if (!fs.existsSync(src)) throw new Error(`missing in the repository: ${item}`);
      copyTree(src, path.join(tmp, item));
    }
    fs.writeFileSync(path.join(tmp, BUNDLE_MARKER), `frontier-orchestration bundle ${version}\n`);
    const aside = `${tmp}.old`;
    let moved = false;
    // Ownership: the rename-aside and the delete below touch only a folder with BUNDLE_MARKER.
    // checkOut checked it; this checks it again just before the rename.
    if (lexists(out) && !ownBundle(out)) {
      throw new RefusedOutput(`refusing --out ${out}: the folder exists and this script did not write it (no ${BUNDLE_MARKER} file). Delete it or use another folder.`);
    }
    try {
      fs.renameSync(out, aside);
      moved = true;
    } catch (e) {
      if (e.code !== 'ENOENT') throw e;
    }
    try {
      fs.renameSync(tmp, out);
    } catch (e) {
      // Put the old bundle back, so a failed build does not leave the package without one.
      if (moved) fs.renameSync(aside, out);
      throw e;
    }
    if (moved) {
      if (!ownBundle(aside)) throw new Error(`${aside} has no ${BUNDLE_MARKER} file, so it is left in place. Delete it by hand.`);
      fs.rmSync(aside, { recursive: true, force: true });
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true }); // made by this run
  }
  return { out, version };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const i = process.argv.indexOf('--out');
  const out = i > 0 && process.argv[i + 1] ? path.resolve(process.argv[i + 1]) : DEFAULT_OUT;
  try {
    const r = buildBundle(REPO_ROOT, out);
    console.log(`bundle ${r.version} written to ${path.relative(process.cwd(), r.out) || '.'}`);
  } catch (e) {
    console.error(`bundle: ${e.message}`);
    process.exit(e instanceof RefusedOutput ? 2 : 1);
  }
}
