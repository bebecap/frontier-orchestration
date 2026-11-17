// Test helpers: a staged copy of the package, fake host CLIs that record their argv, and an
// isolated HOME. No network, no npm install, no real host CLI.

import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildBundle, REPO_ROOT } from '../scripts/bundle.mjs';

export const NPM_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export { REPO_ROOT };
export const VERSION = JSON.parse(fs.readFileSync(path.join(NPM_DIR, 'package.json'), 'utf8')).version;
export const FO_REL = path.join('skills', 'frontier-orchestration', 'scripts', 'fo.py');

export function tmpdir(prefix = 'fo-npx-') {
  // realpath: on macOS the temp folder is a symbolic link; paths in argv must compare equal.
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

let staged = null;
/**
 * A private copy of the package (bin, lib, package.json, a fresh bundle). Each test file gets its
 * own copy, so a parallel `npm pack` that rebuilds npm/bundle cannot race with it.
 */
export function stagePackage() {
  if (staged) return staged;
  const dir = tmpdir('fo-npx-pkg-');
  for (const sub of ['bin', 'lib']) fs.cpSync(path.join(NPM_DIR, sub), path.join(dir, sub), { recursive: true });
  fs.copyFileSync(path.join(NPM_DIR, 'package.json'), path.join(dir, 'package.json'));
  buildBundle(REPO_ROOT, path.join(dir, 'bundle'));
  staged = { dir, cli: path.join(dir, 'bin', 'cli.mjs'), bundle: path.join(dir, 'bundle') };
  return staged;
}

/**
 * A sandbox: HOME, a bin folder with fake CLIs, and a log of every fake call (outside HOME).
 * opts.hosts: fake host CLIs to create. opts.fail: hosts whose fake exits 1.
 * opts.python: 'real' (use /usr/bin/python3 through PATH), a version string such as '3.12' for a
 * fake python3, or null for no python3 at all.
 * opts.skillsDir: create ~/.agents/skills.
 * opts.stateful: the fake host CLIs keep state, as the real CLIs do (see STATEFUL_HOST).
 */
export function sandbox({ hosts = ['claude', 'codex', 'agy'], fail = [], python = '3.12', skillsDir = true, homeName = 'home', stateful = false } = {}) {
  const root = tmpdir();
  const home = path.join(root, homeName);
  const bin = path.join(root, 'bin');
  const log = path.join(root, 'calls.log');
  const state = path.join(root, 'state');
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(bin);
  fs.mkdirSync(state);
  if (skillsDir) fs.mkdirSync(path.join(home, '.agents', 'skills'), { recursive: true });
  const record = '{ printf \'%s\' "${0##*/}"; for a in "$@"; do printf \'\\t%s\' "$a"; done; printf \'\\n\'; } >> "$FAKE_LOG"';
  const writeHost = (h, failing) => fs.writeFileSync(path.join(bin, h),
    stateful ? STATEFUL_HOST.replace('#RECORD#', record)
      : `#!/bin/sh\n${record}\n${STATELESS_LIST}\n${failing ? `echo "fake ${h} failure" >&2\nexit 1` : 'exit 0'}\n`, { mode: 0o755 });
  for (const h of hosts) writeHost(h, fail.includes(h));
  if (typeof python === 'string' && python !== 'real') {
    fs.writeFileSync(path.join(bin, 'python3'),
      `#!/bin/sh\nif [ "$1" = "-I" ] && [ "$2" = "-c" ]; then echo "${python}"; exit 0; fi\n${record}\nexit 0\n`, { mode: 0o755 });
  }
  // python === null: PATH holds only the fake bin folder, so no python3 is found.
  const PATH = python === null ? bin : [bin, '/usr/bin', '/bin'].join(path.delimiter);
  const env = { HOME: home, PATH, FAKE_LOG: log, FAKE_STATE: state, LANG: 'C' };
  return {
    root, home, bin, log, env,
    pluginRoot: path.join(home, '.local', 'share', 'frontier-orchestration', 'plugin'),
    folder: path.join(home, '.local', 'share', 'frontier-orchestration', 'plugin', VERSION),
    skillsTarget: path.join(home, '.agents', 'skills', 'frontier-orchestration'),
    record: path.join(home, '.local', 'share', 'frontier-orchestration', 'plugin', 'installed-hosts.json'),
    /** Creates or replaces a fake host CLI. */
    setHost(h, { failing = false } = {}) {
      writeHost(h, failing);
    },
    /** The hosts in the record, in record order. */
    recordedHosts() {
      return Object.keys(this.recordJson().hosts);
    },
    recordJson() {
      return JSON.parse(fs.readFileSync(this.record, 'utf8'));
    },
    /** Stateful fakes: what the host has registered now. */
    hostState(h) {
      const list = (sub) => {
        const dir = path.join(state, h, sub);
        return fs.existsSync(dir) ? fs.readdirSync(dir).sort() : [];
      };
      return { plugins: list('p'), marketplaces: list('m') };
    },
    /** Stateful fakes: the calls whose arguments start with this text exit 1. */
    failOn(h, args) {
      fs.writeFileSync(path.join(state, `${h}.fail`), args);
    },
    /** Stateful fakes: the call whose arguments start with this text waits (its pid goes to hung.pid). */
    hangOn(h, args) {
      fs.writeFileSync(path.join(state, `${h}.hang`), args);
    },
    /** Stateful fakes: a marketplace with this name that points at a location (another source). */
    addMarketplace(h, name, location) {
      fs.mkdirSync(path.join(state, h, 'm'), { recursive: true });
      fs.writeFileSync(path.join(state, h, 'm', name), location);
    },
    clearFaults() {
      for (const n of fs.readdirSync(state)) if (/\.(fail|hang)$/.test(n)) fs.rmSync(path.join(state, n));
    },
    hungPidFile: path.join(state, 'hung.pid'),
    calls() {
      if (!fs.existsSync(log)) return [];
      return fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => l.split('\t'));
    },
  };
}

/**
 * Stateless fakes answer each list command with an empty list (nothing is registered), also when
 * the fake fails: the installer reads the marketplace list before "marketplace add".
 */
const STATELESS_LIST = 'case " $* " in *" list "*) echo "[]"; exit 0;; esac';

/**
 * A fake host CLI that keeps state in $FAKE_STATE/<host>, as the real CLIs do: "add" and
 * "install" register. On claude and codex, "remove" and "uninstall" fail (exit 1) for a name that
 * is not registered; "agy plugin uninstall" exits 0 for a plugin that is not there, as the real
 * agy does. "list --json" prints the output format of the real CLI (read from its --help and its
 * output on a real machine): the marketplace list gives "installLocation" (claude) or "root"
 * (codex), and the agy list gives "name", "enabled", "version", "source" and "path".
 * "agy plugin install <folder>" copies the folder, as the real agy does (to
 * ~/.gemini/config/plugins/<name>); the list "path" is that copy.
 * "marketplace add" fails when a marketplace with that name points at another folder.
 * "plugin install" on claude and "plugin add" on codex need the marketplace.
 * $FAKE_STATE/<host>.fail and <host>.hang hold the start of the arguments of a call that fails
 * or waits.
 */
export const STATEFUL_HOST = `#!/bin/sh
#RECORD#
h="\${0##*/}"
S="$FAKE_STATE/$h"
mkdir -p "$S/p" "$S/m"
all="$*"
if [ -f "$FAKE_STATE/$h.hang" ]; then
  pat="$(cat "$FAKE_STATE/$h.hang")"
  case "$all" in "$pat"*) echo $$ > "$FAKE_STATE/hung.pid"; exec sleep 60;; esac
fi
if [ -f "$FAKE_STATE/$h.fail" ]; then
  pat="$(cat "$FAKE_STATE/$h.fail")"
  case "$all" in "$pat"*) echo "fake $h failure" >&2; exit 1;; esac
fi
json_list() {
  first=1
  printf '['
  for f in "$1"/*; do
    [ -e "$f" ] || continue
    [ $first = 1 ] || printf ','
    first=0
    printf '{"%s":"%s"}' "$2" "\${f##*/}"
  done
  printf ']'
}
mp_list() {
  first=1
  printf '['
  for f in "$S/m"/*; do
    [ -e "$f" ] || continue
    [ $first = 1 ] || printf ','
    first=0
    printf '{"name":"%s","%s":"%s"}' "\${f##*/}" "$1" "$(cat "$f")"
  done
  printf ']'
}
agy_list() {
  first=1
  printf '['
  for f in "$S/p"/*; do
    [ -e "$f" ] || continue
    [ $first = 1 ] || printf ','
    first=0
    printf '{"name":"%s","enabled":true,"version":"0.1.0","source":"antigravity","path":"%s"}' "\${f##*/}" "$S/c/\${f##*/}"
  done
  printf ']'
}
case "$h:$1:$2:$3" in
  claude:plugin:marketplace:add|codex:plugin:marketplace:add)
    m="$S/m/frontier-orchestration"
    if [ -e "$m" ] && [ "$(cat "$m")" != "$4" ]; then echo "marketplace frontier-orchestration already exists" >&2; exit 1; fi
    printf '%s' "$4" > "$m";;
  claude:plugin:marketplace:remove|codex:plugin:marketplace:remove)
    [ -e "$S/m/$4" ] || { echo "marketplace $4 not found" >&2; exit 1; }
    rm "$S/m/$4";;
  claude:plugin:marketplace:list) mp_list installLocation; echo;;
  codex:plugin:marketplace:list) printf '{"marketplaces":'; mp_list root; printf '}\\n';;
  claude:plugin:install:*|codex:plugin:add:*)
    [ -e "$S/m/\${3#*@}" ] || { echo "marketplace \${3#*@} not found" >&2; exit 1; }
    touch "$S/p/$3";;
  agy:plugin:install:*)
    rm -rf "$S/c/frontier-orchestration"; mkdir -p "$S/c"; cp -R "$3" "$S/c/frontier-orchestration"
    touch "$S/p/frontier-orchestration";;
  agy:plugin:uninstall:*) rm -f "$S/p/$3"; rm -rf "$S/c/$3";;
  claude:plugin:uninstall:*|codex:plugin:remove:*)
    [ -e "$S/p/$3" ] || { echo "plugin $3 is not installed" >&2; exit 1; }
    rm "$S/p/$3";;
  claude:plugin:list:*) json_list "$S/p" id; echo;;
  agy:plugin:list:*) agy_list; echo;;
  codex:plugin:list:*) printf '{"installed":'; json_list "$S/p" pluginId; printf ',"available":[]}\\n';;
  *) echo "fake $h: unknown command $all" >&2; exit 2;;
esac
exit 0
`;

/** Runs the staged CLI (not a terminal: stdin is /dev/null). */
export function runCli(sb, args, cli = stagePackage().cli) {
  const r = spawnSync(process.execPath, [cli, ...args], { env: sb.env, cwd: sb.home, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000 });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr, all: r.stdout + r.stderr };
}

/** Every path under a folder with its size and mtime, to prove a run changed nothing. */
export function snapshot(dir) {
  const out = {};
  const walk = (d) => {
    for (const name of fs.readdirSync(d).sort()) {
      const p = path.join(d, name);
      const st = fs.lstatSync(p);
      out[path.relative(dir, p)] = st.isDirectory() ? 'dir' : `${st.size}:${st.mtimeMs}`;
      if (st.isDirectory()) walk(p);
    }
  };
  walk(dir);
  return out;
}

/** Relative file list of a tree. */
export function files(dir) {
  return Object.entries(snapshot(dir)).filter(([, v]) => v !== 'dir').map(([k]) => k).sort();
}

export { execFileSync };
