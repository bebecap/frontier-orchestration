// frontier-orchestration npx installer (prototype).
//
// Security rules in this file:
// - Every external command runs through execFile or spawn with an argument array. No shell.
// - No argument is quoted by hand for execution. The quoting in show() is for display only.
// - The installer refuses to run as root.
// - Each command is printed before it runs.

import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';

export const PLUGIN = 'frontier-orchestration';
export const MARKETPLACE = 'frontier-orchestration';
export const PLUGIN_ID = `${PLUGIN}@${MARKETPLACE}`;
export const HOSTS = ['claude', 'codex', 'agy', 'skills'];
export const CLI_HOSTS = ['claude', 'codex', 'agy'];
// A file that says this installer put a folder there: in the shared skills copy, and in each
// version folder (a host that copies the plugin folder, such as agy, keeps it in its copy).
export const MARKER = '.installed-by-frontier-orchestration-npx';
export const MIN_PYTHON = [3, 9];
// The file in the plugin root folder that lists the hosts this installer registered.
export const RECORD = 'installed-hosts.json';
// The lock file, in the parent of the plugin root folder: one install or uninstall at a time.
export const LOCK = '.installer.lock';

const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FO_REL = path.join('skills', 'frontier-orchestration', 'scripts', 'fo.py');
const HOST_TIMEOUT_MS = 5 * 60 * 1000;
// The exit code that runFile gives a host command that it stopped at the timeout (as timeout(1)).
const TIMEOUT_CODE = 124;
const PROBE_TIMEOUT_MS = 60 * 1000;
const VERSION_RE = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;

const HELP = `Usage: frontier-orchestration [command] [flags]

Commands:
  install     (default) copy the plugin to a stable folder, register it with each host,
              then start the interactive setup (fo.py install)
  uninstall   remove the plugin from each host that this installer registered, then delete
              the copied plugin folders (your config and your ledger stay)
  doctor      run fo.py doctor from the installed folder (add --live for live checks)

Flags:
  --hosts=claude,codex,agy,skills   hosts to use (install default: every host that is found;
                                    uninstall default: every host that install recorded)
  --dry-run                         print every step, change nothing
  --yes, -y                         do not ask before changes
  --no-setup                        do not start the Python setup after install
  --live                            doctor only: pass --live to fo.py doctor
  --force                           uninstall only: without a record, try every host that is
                                    found, then delete the plugin folders (--hosts is then
                                    refused); with a record, drop the steps that the host cannot
                                    check from the record (they stay on the host)
  --break-lock                      install and uninstall only: delete the lock file first. Use
                                    it only when no installer is running
  --help, -h                        show this help
  --version                         show the package version
`;

export class UsageError extends Error {}

export function parseArgs(argv) {
  const o = { command: 'install', hosts: null, dryRun: false, yes: false, setup: true, live: false, force: false, breakLock: false, help: false, version: false };
  let commandSeen = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--help' || a === '-h') o.help = true;
    else if (a === '--version') o.version = true;
    else if (a === '--dry-run') o.dryRun = true;
    else if (a === '--yes' || a === '-y') o.yes = true;
    else if (a === '--no-setup') o.setup = false;
    else if (a === '--live') o.live = true;
    else if (a === '--force') o.force = true;
    else if (a === '--break-lock') o.breakLock = true;
    else if (a === '--hosts' || a.startsWith('--hosts=')) {
      const v = a === '--hosts' ? argv[++i] : a.slice('--hosts='.length);
      if (v === undefined) throw new UsageError('--hosts needs a value, for example --hosts=claude,codex');
      const list = v.split(',').map((s) => s.trim()).filter(Boolean);
      const bad = list.filter((h) => !HOSTS.includes(h));
      if (!list.length || bad.length) throw new UsageError(`unknown host ${JSON.stringify(bad[0] ?? v)}; use ${HOSTS.join(', ')}`);
      o.hosts = [...new Set(list)];
    } else if (!a.startsWith('-') && !commandSeen && ['install', 'uninstall', 'doctor'].includes(a)) {
      o.command = a;
      commandSeen = true;
    } else {
      throw new UsageError(`unknown argument ${JSON.stringify(a)}; run with --help`);
    }
  }
  if (o.live && o.command !== 'doctor') throw new UsageError('--live is a doctor flag');
  if (o.force && o.command !== 'uninstall') throw new UsageError('--force is an uninstall flag');
  if (o.breakLock && o.command === 'doctor') throw new UsageError('--break-lock is an install and uninstall flag');
  if (o.breakLock && o.dryRun) throw new UsageError('--break-lock cannot be used with --dry-run (a dry run takes no lock)');
  return o;
}

/** Display form of a command. Never used to run anything. */
export function show(file, args) {
  return [file, ...args].map((a) => (/^[\w@%+=:,./-]+$/.test(a) ? a : JSON.stringify(a))).join(' ');
}

/** Finds an executable on PATH. Relative PATH entries are ignored. */
export function which(name, env) {
  for (const dir of (env.PATH || '').split(path.delimiter)) {
    if (!dir || !path.isAbsolute(dir)) continue;
    const p = path.join(dir, name);
    try {
      if (fs.statSync(p).isFile()) {
        fs.accessSync(p, fs.constants.X_OK);
        return p;
      }
    } catch {
      // not here
    }
  }
  return null;
}

// The steps of each host. A step has a key: the record (installed-hosts.json) keeps the state of
// each step by its key, and uninstall runs only the remove steps whose keys are in the record.
export const STEP_KEYS = {
  claude: ['marketplace', 'plugin'],
  codex: ['marketplace', 'plugin'],
  agy: ['plugin'],
  skills: ['copy'],
};

export function installSteps(host, folder) {
  switch (host) {
    case 'claude':
      return [
        { key: 'marketplace', args: ['plugin', 'marketplace', 'add', folder], soft: true },
        { key: 'plugin', args: ['plugin', 'install', PLUGIN_ID] },
      ];
    case 'codex':
      return [
        { key: 'marketplace', args: ['plugin', 'marketplace', 'add', folder], soft: true },
        { key: 'plugin', args: ['plugin', 'add', PLUGIN_ID] },
      ];
    case 'agy':
      return [{ key: 'plugin', args: ['plugin', 'install', folder] }];
    default:
      throw new Error(`no install steps for ${host}`);
  }
}

/** The entries of a list, from a JSON array or from the first array field that is named. */
function entries(j, ...fields) {
  if (Array.isArray(j)) return j;
  for (const f of fields) if (j && Array.isArray(j[f])) return j[f];
  return null;
}

const NAME_KEYS = ['id', 'pluginId', 'name'];

/** The names of an entry: the entry itself (a string), or its fields that hold a name. */
function names(e) {
  if (typeof e === 'string') return [e];
  if (!e || typeof e !== 'object') return [];
  return NAME_KEYS.filter((k) => typeof e[k] === 'string').map((k) => e[k]);
}

/**
 * True when an entry has one of the names; false when not. null (unknown) when the output is not
 * a list, or when the list has entries but no entry has a name this installer knows: an empty
 * list says "not there", a list in an unknown format says nothing.
 */
function listed(list, test) {
  if (!list) return null;
  if (list.length && !list.some((e) => names(e).length)) return null;
  return list.some((e) => names(e).some(test));
}

// The list commands that tell if a step is in place, and where it comes from. Each one was read
// from the --help of the real CLI (claude, codex and agy all have "plugin list --json"). A list
// that cannot be read gives null: the state is then unknown.
const PROBES = {
  'claude/plugin': { args: ['plugin', 'list', '--json'], entries: (j) => entries(j), match: (n) => n === PLUGIN_ID },
  'claude/marketplace': { args: ['plugin', 'marketplace', 'list', '--json'], entries: (j) => entries(j), match: (n) => n === MARKETPLACE },
  'codex/plugin': {
    args: ['plugin', 'list', '--json'],
    entries: (j) => entries(j, 'installed')?.filter((e) => !e || e.installed !== false) ?? null,
    match: (n) => n === PLUGIN_ID,
  },
  'codex/marketplace': { args: ['plugin', 'marketplace', 'list', '--json'], entries: (j) => entries(j, 'marketplaces'), match: (n) => n === MARKETPLACE },
  'agy/plugin': {
    args: ['plugin', 'list', '--json'],
    entries: (j) => entries(j, 'plugins', 'installed'),
    match: (n) => n === PLUGIN || n.startsWith(`${PLUGIN}@`),
  },
};

/** True when target is the folder base or a path inside it. */
/**
 * The absolute paths that a list entry gives for its location. The field names come from the
 * output of the real CLIs: claude marketplaces "installLocation", claude plugins "installPath",
 * codex marketplaces "root", codex plugins "source.path" and "marketplaceSource.source", agy
 * plugins "path".
 */
function locations(e) {
  if (!e || typeof e !== 'object') return [];
  const out = [];
  const add = (v) => {
    if (typeof v === 'string' && path.isAbsolute(v)) out.push(v);
  };
  for (const k of ['installLocation', 'root', 'path', 'installPath', 'source']) add(e[k]);
  for (const k of ['source', 'marketplaceSource']) {
    if (e[k] && typeof e[k] === 'object') {
      add(e[k].path);
      add(e[k].source);
    }
  }
  return [...new Set(out)];
}

/** Runs a list command. Returns {list} (an array), or {list: null, why} when it cannot be read. */
async function readList(ctx, exe, probe) {
  const { d } = ctx;
  d.out(`  $ ${show(exe, probe.args)}`);
  const r = await d.runFile(exe, probe.args, d.env, PROBE_TIMEOUT_MS);
  if (r.code !== 0) return { list: null, why: `"${probe.args.join(' ')}" exit ${r.code}` };
  const j = parseJson(r.stdout);
  const list = j === undefined ? null : probe.entries(j);
  if (!list || listed(list, probe.match) === null) return { list: null, why: 'the output is not a list that this installer knows' };
  return { list };
}

/**
 * Who owns a step on a host now. Reads the list of the host; it never trusts the record.
 * - 'absent': the list does not show it.
 * - 'ours': its location is a folder that this installer made: ownedDir() is true (a real folder,
 *   not a symbolic link, that holds MARKER). A version folder holds MARKER, and so does the copy
 *   that agy makes of it (~/.gemini/config/plugins/<name>). The place of the folder does not
 *   count: a folder in the plugin root without MARKER (for example a checkout of the user) is not
 *   ours. A claude or codex plugin is also ours when the marketplace that it comes from is ours.
 * - 'foreign': it has a location, and that location is not ours (another source, for example
 *   GitHub, or a folder without MARKER). loc gives the location.
 * - 'unknown': the list cannot be read, or the entry gives no location. why gives the cause.
 */
async function ownership(ctx, exe, host, key) {
  const probe = PROBES[`${host}/${key}`];
  const r = await readList(ctx, exe, probe);
  if (!r.list) return { state: 'unknown', why: r.why };
  const e = r.list.find((x) => names(x).some(probe.match));
  if (!e) return { state: 'absent' };
  const locs = locations(e);
  const own = locs.find((l) => ownedDir(l));
  if (own) return { state: 'ours', loc: own };
  if (key === 'plugin' && STEP_KEYS[host].includes('marketplace')) {
    // claude keeps a plugin in its own cache, so the plugin location does not tell. The plugin id
    // names the marketplace, and the marketplace location tells.
    const m = await ownership(ctx, exe, host, 'marketplace');
    if (m.state === 'ours' || m.state === 'foreign') return m;
    return { state: 'unknown', why: m.state === 'absent' ? `the marketplace ${MARKETPLACE} is not listed, so the source of the plugin is not known` : m.why };
  }
  if (!locs.length) return { state: 'unknown', why: 'the list gives no location for it' };
  return { state: 'foreign', loc: locs[0] };
}

export function removeSteps(host) {
  const probe = (key) => PROBES[`${host}/${key}`];
  switch (host) {
    case 'claude':
      return [
        { key: 'plugin', args: ['plugin', 'uninstall', PLUGIN_ID], probe: probe('plugin') },
        { key: 'marketplace', args: ['plugin', 'marketplace', 'remove', MARKETPLACE], probe: probe('marketplace') },
      ];
    case 'codex':
      return [
        { key: 'plugin', args: ['plugin', 'remove', PLUGIN_ID], probe: probe('plugin') },
        { key: 'marketplace', args: ['plugin', 'marketplace', 'remove', MARKETPLACE], probe: probe('marketplace') },
      ];
    case 'agy':
      return [{ key: 'plugin', args: ['plugin', 'uninstall', PLUGIN], probe: probe('plugin') }];
    default:
      throw new Error(`no remove steps for ${host}`);
  }
}

/**
 * Runs a file with an argument array (no shell). Never throws for a non-zero exit.
 * The child gets no input: its stdin is closed at once. Only a CLI that reads its answer from
 * standard input gets end of input and stops at once. A CLI that asks on /dev/tty (the terminal)
 * still waits for the timeout.
 * At the timeout, the child gets SIGTERM, then SIGKILL after KILL_GRACE_MS (stopChild). The result
 * comes when the child has exited, and it is always a failure (code 124) that says "timeout",
 * also when the child exits 0 after SIGTERM.
 * After SIGINT or SIGTERM under the lock, it starts nothing and never resolves (the run stops).
 */
export function runFile(file, args, env, timeoutMs = HOST_TIMEOUT_MS) {
  if (stopping) return new Promise(() => {});
  return new Promise((resolve) => {
    let timedOut = false;
    let settled = false;
    const timeoutResult = (stdout, stderr) => {
      const msg = String(stderr ?? '');
      return { code: TIMEOUT_CODE, stdout: String(stdout ?? ''), stderr: `${msg}${msg ? '\n' : ''}stopped after ${Math.round(timeoutMs / 1000)} s (timeout)` };
    };
    const settle = (r) => {
      if (settled || stopping) return; // stopping: the run stops, the caller does not go on
      settled = true;
      clearTimeout(timer);
      resolve(r);
    };
    const child = execFile(file, args, { env, shell: false, maxBuffer: 16 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (timedOut) {
        settle(timeoutResult(stdout, stderr));
        return;
      }
      let code = 0;
      if (error) code = typeof error.code === 'number' ? error.code : 1;
      let msg = String(stderr ?? '');
      if (!msg && error && typeof error.code !== 'number') msg = error.message;
      settle({ code, stdout: String(stdout ?? ''), stderr: msg });
    });
    const timer = setTimeout(() => {
      timedOut = true;
      // SIGTERM, then SIGKILL. A process that the child started can keep its output pipes open
      // after the child is dead, so the result comes at the exit of the child, not at the end of
      // its output.
      stopChild(child).then(() => {
        child.stdout?.destroy();
        child.stderr?.destroy();
        settle(timeoutResult('', ''));
      });
    }, timeoutMs);
    children.add(child);
    child.once('exit', () => children.delete(child));
    if (child.stdin) {
      child.stdin.on('error', () => {}); // the child can exit before the end is written
      child.stdin.end();
    }
  });
}

/** Runs a file with the terminal attached (no shell). Resolves to the exit code. */
function runInherit(file, args, env) {
  return new Promise((resolve) => {
    const child = spawn(file, args, { env, shell: false, stdio: 'inherit' });
    child.on('error', () => resolve(127));
    child.on('close', (code, signal) => resolve(code ?? (signal ? 128 : 1)));
  });
}

/** Copies a folder tree into an existing empty folder. Symbolic links are refused. */
export function copyDir(src, dst) {
  for (const name of fs.readdirSync(src)) {
    const s = path.join(src, name);
    const d = path.join(dst, name);
    const st = fs.lstatSync(s);
    if (st.isSymbolicLink()) throw new Error(`refusing to copy a symbolic link: ${s}`);
    if (st.isDirectory()) {
      fs.mkdirSync(d, { mode: 0o700 });
      copyDir(s, d);
    } else if (st.isFile()) {
      fs.copyFileSync(s, d, fs.constants.COPYFILE_EXCL);
      fs.chmodSync(d, st.mode & 0o777);
    }
  }
}

async function defaultAsk(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    return (await rl.question(question)).trim();
  } finally {
    rl.close();
  }
}

function lastLine(text) {
  const lines = String(text).trim().split('\n').filter(Boolean);
  return lines.length ? lines[lines.length - 1].slice(0, 300) : '';
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function packageVersion() {
  return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8')).version;
}

/** All paths this installer uses, from HOME (and XDG_DATA_HOME, as fo_config.py does). */
export function paths(env, version) {
  const home = env.HOME;
  if (!home || !path.isAbsolute(home)) throw new Error('HOME is not set to an absolute path');
  const xdg = (env.XDG_DATA_HOME || '').trim();
  const dataHome = xdg && path.isAbsolute(xdg) ? xdg : path.join(home, '.local', 'share');
  const pluginRoot = path.join(dataHome, 'frontier-orchestration', 'plugin');
  return {
    home,
    pluginRoot,
    folder: path.join(pluginRoot, version),
    record: path.join(pluginRoot, RECORD),
    lock: path.join(dataHome, 'frontier-orchestration', LOCK),
    // The temporary copy for the skills host is made here, outside the folder that agents scan.
    skillsStage: path.join(home, '.agents'),
    skillsParent: path.join(home, '.agents', 'skills'),
    skillsTarget: path.join(home, '.agents', 'skills', 'frontier-orchestration'),
  };
}

function sameDevice(a, b) {
  try {
    return fs.statSync(a).dev === fs.statSync(b).dev;
  } catch {
    return false;
  }
}

/**
 * The names that installTree gives its temporary and set-aside folders for one target name:
 * ".tmp-<name>-", then the 6 characters that mkdtemp adds, then ".old" or nothing. An exact
 * match, so the leftovers of "0.1.0-beta" never match the target "0.1.0".
 */
export function leftoverPattern(name) {
  return new RegExp(`^\\.tmp-${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-[A-Za-z0-9]{6}(?:\\.old)?$`);
}

// ---- Ownership: what the installer may delete ----
//
// The installer deletes a folder recursively only when it can prove that it made that folder:
// - the folder holds MARKER (each version folder, the skills copy, and each temporary or
//   set-aside folder of installTree get it), or
// - the folder is a temporary folder that this run made (installTree before the marker is in it).
// The plugin root folder itself is deleted only with rmdir (an empty folder). Anything else is
// kept and listed to the user.

function lexists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

/**
 * True when dir is a real folder (not a symbolic link) that holds MARKER as a file. This is the
 * one test of "this installer made this folder". It decides what may be deleted, and also which
 * host registration is ours (ownership()): before a registration at install, and before each
 * removal at uninstall. It uses lstat, so it does not follow a symbolic link.
 */
export function ownedDir(dir) {
  try {
    return fs.lstatSync(dir).isDirectory() && fs.lstatSync(path.join(dir, MARKER)).isFile();
  } catch {
    return false;
  }
}

/**
 * Deletes the temporary and set-aside folders that a killed run of installTree left in dir: a
 * folder with the marker recursively, an empty folder with rmdir. Returns the paths with a
 * leftover name that it keeps (no marker and not empty, or not a folder).
 */
function removeLeftovers(dir, name) {
  const re = leftoverPattern(name);
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  const kept = [];
  for (const n of names) {
    if (!re.test(n)) continue;
    const full = path.join(dir, n);
    try {
      if (ownedDir(full)) fs.rmSync(full, { recursive: true, force: true });
      else fs.rmdirSync(full);
    } catch {
      kept.push(full);
    }
  }
  return kept;
}

/**
 * Atomic, versioned copy: copy into a temporary folder, then rename it into place.
 * If the copy fails, the target is not created (or the earlier copy stays as it was).
 *
 * stageDir holds the temporary folder and the set-aside earlier copy. The default is the parent
 * of dest. Use a folder outside a folder that agents scan, so that a killed run cannot leave a
 * second SKILL.md where agents look. stageDir must be on the same file system as dest (rename
 * cannot move across file systems); if it is not, the parent of dest is used.
 * Each run first deletes the leftovers of a killed earlier run (see leftoverPattern) that hold
 * MARKER or are empty.
 *
 * Ownership: the temporary folder gets MARKER before the copy, so a leftover of a killed run can
 * be proved to be ours. An existing dest is replaced only when it holds MARKER; otherwise
 * installTree throws and changes nothing. Returns the leftover paths that it keeps.
 */
export function installTree(src, dest, copy, extra, stageDir = null) {
  const parent = path.dirname(dest);
  const prefix = `.tmp-${path.basename(dest)}-`;
  const notOurs = () => new Error(`${dest} exists and this installer did not create it (no ${MARKER} file in it). It is left in place. Move it, then run this command again.`);
  if (lexists(dest) && !ownedDir(dest)) throw notOurs();
  fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
  const stage = stageDir && sameDevice(stageDir, parent) ? stageDir : parent;
  const kept = removeLeftovers(parent, path.basename(dest));
  if (stage !== parent) kept.push(...removeLeftovers(stage, path.basename(dest)));
  const tmp = fs.mkdtempSync(path.join(stage, prefix));
  try {
    fs.writeFileSync(path.join(tmp, MARKER), '');
    copy(src, tmp);
    if (extra) extra(tmp);
  } catch (e) {
    fs.rmSync(tmp, { recursive: true, force: true }); // made by this run
    throw e;
  }
  let aside = null;
  if (lexists(dest)) {
    // Checked again just before the rename: the rename-aside moves only a folder that is ours.
    if (!ownedDir(dest)) {
      fs.rmSync(tmp, { recursive: true, force: true }); // made by this run
      throw notOurs();
    }
    aside = `${tmp}.old`;
    fs.renameSync(dest, aside);
  }
  try {
    fs.renameSync(tmp, dest);
  } catch (e) {
    if (aside) fs.renameSync(aside, dest);
    fs.rmSync(tmp, { recursive: true, force: true }); // made by this run
    throw e;
  }
  if (aside) {
    if (ownedDir(aside)) fs.rmSync(aside, { recursive: true, force: true });
    else kept.push(aside);
  }
  return kept;
}

/** Prints the leftover paths that installTree kept. */
function noteKept(d, kept) {
  for (const k of kept) d.out(`    left in place (no ${MARKER} file, not empty): ${k}`);
}

const FIX_PYTHON = [
  'Python 3.9 or newer must be on PATH as python3. To fix:',
  '  macOS: run "xcode-select --install", or use the installer from https://www.python.org/downloads/',
  '  Debian or Ubuntu: run "sudo apt install python3"',
  '  Fedora: run "sudo dnf install python3"',
  'Then run this command again.',
];

export async function main(argv, overrides = {}) {
  const d = {
    env: process.env,
    getuid: () => (typeof process.getuid === 'function' ? process.getuid() : -1),
    platform: process.platform,
    out: (s) => process.stdout.write(`${s}\n`),
    err: (s) => process.stderr.write(`${s}\n`),
    isTTY: Boolean(process.stdin.isTTY && process.stdout.isTTY),
    ask: defaultAsk,
    runFile,
    runInherit,
    copyDir,
    bundleDir: path.join(PKG_ROOT, 'bundle'),
    version: null,
    ...overrides,
  };

  let o;
  try {
    o = parseArgs(argv);
  } catch (e) {
    if (!(e instanceof UsageError)) throw e;
    d.err(`frontier-orchestration: ${e.message}`);
    return 2;
  }
  if (o.help) {
    d.out(HELP);
    return 0;
  }
  const version = d.version ?? packageVersion();
  if (o.version) {
    d.out(version);
    return 0;
  }
  if (d.getuid() === 0) {
    d.err('frontier-orchestration: refusing to run as root. Run it as your own user, without sudo.');
    return 1;
  }
  if (d.platform === 'win32') {
    d.err('frontier-orchestration: Windows is not supported. The skill needs macOS or Linux.');
    return 1;
  }
  if (!VERSION_RE.test(version)) {
    d.err(`frontier-orchestration: bad package version ${JSON.stringify(version)}`);
    return 1;
  }
  let p;
  try {
    p = paths(d.env, version);
  } catch (e) {
    d.err(`frontier-orchestration: ${e.message}`);
    return 1;
  }
  const ctx = { d, o, version, p };
  if (o.command === 'install') return install(ctx);
  if (o.command === 'uninstall') return uninstall(ctx);
  return doctor(ctx);
}

function detect(ctx) {
  const { d, p } = ctx;
  const found = {};
  for (const h of CLI_HOSTS) found[h] = which(h, d.env);
  found.skills = isDir(p.skillsParent) ? p.skillsParent : null;
  return found;
}

function chooseHosts(ctx, found) {
  const { d, o } = ctx;
  const hosts = o.hosts ?? HOSTS.filter((h) => found[h]);
  const missing = HOSTS.filter((h) => !found[h]);
  d.out(`Hosts: ${hosts.length ? hosts.join(', ') : 'none'}${missing.length ? ` (not found: ${missing.join(', ')})` : ''}`);
  return hosts;
}

/** False (with the message) when the run must ask but cannot: no --yes, no --dry-run, no terminal. */
function canConfirm(ctx) {
  const { d, o } = ctx;
  if (o.yes || o.dryRun || d.isTTY) return true;
  d.err('frontier-orchestration: this is not a terminal, so it cannot ask. Pass --yes to continue.');
  return false;
}

async function confirm(ctx, question) {
  const { d, o } = ctx;
  if (!canConfirm(ctx)) return false;
  if (o.yes || o.dryRun) return true;
  const a = (await d.ask(`${question} [y/N] `)).toLowerCase();
  return a === 'y' || a === 'yes';
}

async function checkPython(ctx) {
  const { d } = ctx;
  const py = which('python3', d.env);
  if (!py) {
    d.err('python3 was not found on PATH.');
    FIX_PYTHON.forEach((l) => d.err(l));
    return null;
  }
  const args = ['-I', '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'];
  d.out(`$ ${show(py, args)}`);
  const r = await d.runFile(py, args, d.env);
  const m = /^(\d+)\.(\d+)/.exec(r.stdout.trim());
  if (r.code !== 0 || !m) {
    d.err(`python3 did not report its version (exit ${r.code}). ${lastLine(r.stderr)}`);
    FIX_PYTHON.forEach((l) => d.err(l));
    return null;
  }
  const [maj, min] = [Number(m[1]), Number(m[2])];
  if (maj < MIN_PYTHON[0] || (maj === MIN_PYTHON[0] && min < MIN_PYTHON[1])) {
    d.err(`python3 is ${maj}.${min}. This skill needs ${MIN_PYTHON.join('.')} or newer.`);
    FIX_PYTHON.forEach((l) => d.err(l));
    return null;
  }
  d.out(`  python3 ${maj}.${min}: OK`);
  return py;
}

function checkBundle(ctx) {
  const { d, version } = ctx;
  const fo = path.join(d.bundleDir, FO_REL);
  const manifest = path.join(d.bundleDir, '.claude-plugin', 'plugin.json');
  if (!fs.existsSync(fo) || !fs.existsSync(manifest)) {
    throw new Error(`the plugin bundle is missing at ${d.bundleDir}. In a checkout, run "node scripts/bundle.mjs" in npm/ first.`);
  }
  const v = JSON.parse(fs.readFileSync(manifest, 'utf8')).version;
  if (v !== version) throw new Error(`the bundle is version ${v}, but the package is ${version}`);
}

/**
 * Runs the install steps of one host. The record gets each step as "pending" before the step
 * runs, and "done" when it succeeds. A run that is stopped (Ctrl-C) thus leaves a record of each
 * step that can be registered. A step that fails stays "pending": uninstall then checks it.
 */
async function installHostSteps(ctx, host, rec) {
  const { d, o, p } = ctx;
  const exe = which(host, d.env);
  if (!exe) return { status: 'skipped', note: `${host} CLI not found on PATH` };
  const notes = [];
  const steps = installSteps(host, p.folder);
  // Ownership first, for every host: before the first step that changes the host (the
  // marketplace step on claude and codex, the plugin step on agy), read the list of the host for
  // each step of that host. Never add over, replace, or record an item with this name from
  // another source, or one whose source this installer cannot see: skip the host, record
  // nothing, and say why. Nothing that changes the host runs before these checks.
  // A claude or codex plugin with this id that stays after its marketplace was removed has an
  // owner that cannot be found (unless its own location holds MARKER): that host is skipped too.
  for (const { key } of steps) {
    const probe = PROBES[`${host}/${key}`];
    if (o.dryRun) {
      d.out(`  (dry run) $ ${show(exe, probe.args)}`);
      continue;
    }
    const own = await ownership(ctx, exe, host, key);
    const what = key === 'marketplace' ? `a marketplace named ${MARKETPLACE}` : `a plugin named ${host === 'agy' ? PLUGIN : PLUGIN_ID}`;
    const remove = removeSteps(host).find((x) => x.key === key);
    let why = null;
    if (own.state === 'foreign') {
      why = `${what} from another source is in place (${own.loc}). To use this copy, remove it ("${host} ${remove.args.join(' ')}"), then run install again`;
    } else if (own.state === 'unknown') {
      why = `this installer cannot tell if ${what} from another source is in place (${own.why})`;
    }
    if (why) {
      d.out(`    ${why}`);
      return { status: 'skipped', note: `${why}. ${host} was not changed` };
    }
  }
  for (const step of steps) {
    if (o.dryRun) {
      d.out(`  (dry run) $ ${show(exe, step.args)}`);
      continue;
    }
    if (stepState(rec, host, step.key) !== 'done') setStep(ctx, rec, host, step.key, 'pending');
    d.out(`  $ ${show(exe, step.args)}`);
    const r = await d.runFile(exe, step.args, d.env);
    if (r.code === 0) {
      setStep(ctx, rec, host, step.key, 'done');
      continue;
    }
    const why = failure(step, r);
    d.out(`    failed: ${why}`);
    if (step.soft) {
      notes.push(`warning: ${why}`);
      continue;
    }
    return { status: 'failed', note: why };
  }
  if (o.dryRun) return { status: 'dry run', note: '' };
  return { status: 'ok', note: notes.join('; ') };
}

function failure(step, r) {
  const last = lastLine(r.stderr || r.stdout);
  return `"${step.args.slice(0, 3).join(' ')}" exit ${r.code}${last ? `: ${last}` : ''}`;
}

function installSkillsCopy(ctx, found, rec) {
  const { d, o, p } = ctx;
  if (!found.skills) return { status: 'skipped', note: `${p.skillsParent} does not exist` };
  const exists = lexists(p.skillsTarget);
  if (exists && !ownedDir(p.skillsTarget)) {
    return { status: 'skipped', note: `${p.skillsTarget} exists and this installer did not create it; left alone` };
  }
  const src = path.join(p.folder, 'skills', 'frontier-orchestration');
  if (o.dryRun) {
    d.out(`  (dry run) copy ${src} to ${p.skillsTarget} (temporary folder, then rename)`);
    return { status: 'dry run', note: '' };
  }
  if (stepState(rec, 'skills', 'copy') !== 'done') setStep(ctx, rec, 'skills', 'copy', 'pending');
  d.out(`  copy ${src} to ${p.skillsTarget}`);
  try {
    noteKept(d, installTree(src, p.skillsTarget, d.copyDir, (tmp) => fs.writeFileSync(path.join(tmp, MARKER), `${ctx.version}\n`), p.skillsStage));
  } catch (e) {
    return { status: 'failed', note: e.message };
  }
  setStep(ctx, rec, 'skills', 'copy', 'done');
  return { status: 'ok', note: `${exists ? 'replaced' : 'created'} ${p.skillsTarget}` };
}

// ---- The record (installed-hosts.json) ----
//
// {"hosts": {"claude": {"marketplace": "done", "plugin": "pending"}, "skills": {"copy": "done"}}}
//
// "done": the step succeeded. "pending": the step started, but did not succeed (it failed, or the
// run stopped). Both count as "possibly registered". A "removeFailures" key from an earlier
// version of this installer is ignored.

const STATES = ['done', 'pending'];

function emptyRecord() {
  return { hosts: {} };
}

/**
 * Reads the record. Returns null when there is no record. Throws when it cannot be read.
 * An earlier format, {"hosts": ["claude", ...]}, is read as "every step of these hosts is done".
 */
function readRecord(p) {
  let text;
  try {
    text = fs.readFileSync(p.record, 'utf8');
  } catch (e) {
    if (e.code === 'ENOENT') return null;
    throw e;
  }
  const j = JSON.parse(text);
  const rec = emptyRecord();
  if (j && Array.isArray(j.hosts)) {
    for (const h of HOSTS) if (j.hosts.includes(h)) rec.hosts[h] = Object.fromEntries(STEP_KEYS[h].map((k) => [k, 'done']));
    return rec;
  }
  if (!j || typeof j.hosts !== 'object' || j.hosts === null) throw new Error(`${p.record} has no "hosts" object`);
  for (const h of HOSTS) {
    const steps = j.hosts[h];
    if (!steps || typeof steps !== 'object') continue;
    const kept = {};
    for (const k of STEP_KEYS[h]) if (STATES.includes(steps[k])) kept[k] = steps[k];
    if (Object.keys(kept).length) rec.hosts[h] = kept;
  }
  return rec;
}

/** Writes the record atomically: a temporary file, then a rename. */
function writeRecord(p, rec) {
  const out = { hosts: {} };
  for (const h of HOSTS) {
    if (!rec.hosts[h]) continue;
    out.hosts[h] = {};
    for (const k of STEP_KEYS[h]) if (rec.hosts[h][k]) out.hosts[h][k] = rec.hosts[h][k];
  }
  fs.mkdirSync(path.dirname(p.record), { recursive: true, mode: 0o700 });
  const tmp = `${p.record}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(out, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(tmp, p.record);
}

function stepState(rec, host, key) {
  return rec.hosts[host] ? rec.hosts[host][key] : undefined;
}

/** Saves the record, unless this run keeps no record (uninstall --force without a record). */
function saveRecord(ctx, rec) {
  if (ctx.persist !== false) writeRecord(ctx.p, rec);
}

function setStep(ctx, rec, host, key, state) {
  (rec.hosts[host] ??= {})[key] = state;
  saveRecord(ctx, rec);
}

function dropStep(ctx, rec, host, key) {
  if (rec.hosts[host]) delete rec.hosts[host][key];
  if (rec.hosts[host] && !Object.keys(rec.hosts[host]).length) delete rec.hosts[host];
  saveRecord(ctx, rec);
}

/** The version folders in the plugin root folder (the folders that a host can point at). */
function versionFolders(p) {
  try {
    return fs.readdirSync(p.pluginRoot).filter((n) => VERSION_RE.test(n) && isDir(path.join(p.pluginRoot, n)));
  } catch {
    return [];
  }
}

// A leftover of installTree for any target name, and a temporary record file of writeRecord.
const ANY_LEFTOVER_RE = /^\.tmp-.+-[A-Za-z0-9]{6}(?:\.old)?$/;
const RECORD_TMP_RE = new RegExp(`^${RECORD.replace(/\./g, '\\.')}\\.tmp-\\d+$`);

/**
 * Sorts the items in the plugin root folder. Returns null when the folder does not exist.
 * owned: what this installer made, with how to delete it ('tree': a version folder or a
 * leftover of installTree that holds MARKER; 'empty': an empty leftover folder; 'file': a
 * temporary record file). foreign: everything else. The record itself is in neither list.
 */
function rootItems(p) {
  let names;
  try {
    names = fs.readdirSync(p.pluginRoot).sort();
  } catch (e) {
    if (e.code === 'ENOENT') return null;
    throw e;
  }
  const owned = [];
  const foreign = [];
  for (const n of names) {
    const full = path.join(p.pluginRoot, n);
    let st;
    try {
      st = fs.lstatSync(full);
    } catch {
      continue; // gone since readdir
    }
    if (n === RECORD && st.isFile()) continue;
    if ((VERSION_RE.test(n) || ANY_LEFTOVER_RE.test(n)) && ownedDir(full)) owned.push({ path: full, how: 'tree' });
    else if (ANY_LEFTOVER_RE.test(n) && st.isDirectory() && !fs.readdirSync(full).length) owned.push({ path: full, how: 'empty' });
    else if (RECORD_TMP_RE.test(n) && st.isFile()) owned.push({ path: full, how: 'file' });
    else foreign.push(full);
  }
  return { owned, foreign };
}

/**
 * Deletes the plugin root folder by ownership: first each item that this installer made, then
 * the record, then the folder itself with rmdir (only an empty folder). A foreign item stays, so
 * the folder stays too. --force does not change this. Returns the paths that stay ([] when the
 * folder is gone or did not exist).
 */
function removePluginRoot(ctx) {
  const { d, p } = ctx;
  const items = rootItems(p);
  if (!items) {
    d.out(`  ${p.pluginRoot} does not exist`);
    return [];
  }
  const kept = [...items.foreign];
  let failed = false;
  for (const it of items.owned) {
    try {
      if (it.how === 'tree') {
        if (!ownedDir(it.path)) throw new Error(`no ${MARKER} file`);
        fs.rmSync(it.path, { recursive: true, force: true });
      } else if (it.how === 'empty') {
        fs.rmdirSync(it.path);
      } else {
        fs.unlinkSync(it.path);
      }
      d.out(`  deleted ${it.path}`);
    } catch (e) {
      failed = true;
      kept.push(`${it.path} (${e.message})`);
    }
  }
  // The record goes only when every item that this installer made is gone. With a failure, the
  // record stays, so a second uninstall finds the folder again.
  if (failed) {
    if (fs.existsSync(p.record)) kept.push(p.record);
    return kept;
  }
  try {
    fs.unlinkSync(p.record);
  } catch (e) {
    if (e.code !== 'ENOENT') return [...kept, `${p.record} (${e.message})`];
  }
  if (kept.length) return kept;
  try {
    fs.rmdirSync(p.pluginRoot); // only an empty folder
    d.out(`  deleted ${p.pluginRoot}`);
    return [];
  } catch (e) {
    // Something came into the folder after the list (or it cannot be deleted): it stays.
    const now = rootItems(p);
    return now ? [...now.owned.map((i) => i.path), ...now.foreign] : [];
  }
}

/** The lines that say what stays in the plugin root folder, and what to do. */
function keptMessage(ctx, kept) {
  const { p } = ctx;
  return [
    `frontier-orchestration: left in place: ${kept.join(', ')}`,
    `This installer deletes only what it made: a folder with the ${MARKER} file, its record and its own temporary files.`,
    `Thus ${p.pluginRoot} stays. --force does not change this.`,
    'Move or delete these items by hand, then run "frontier-orchestration uninstall" again.',
  ];
}

// ---- The lock (.installer.lock) ----
//
// One install or uninstall at a time, across the record updates and the folder changes. Two runs
// at once can lose record updates (each one reads, changes and writes the record), and an
// uninstall can delete the plugin folder while an install registers a host with it.
// The lock file is created with the 'wx' flag (it fails when the file exists). It holds the pid,
// the start time and a random token of the run. A second run does not wait: it exits 1.
// There is no automatic stale-lock takeover. Only the user can say that no installer runs: then
// --break-lock deletes the lock file once, and the run takes the lock with 'wx' as usual.

/** Reads the lock file for the message. Returns {} when it cannot be read or is not JSON. */
function readLock(p) {
  try {
    const j = JSON.parse(fs.readFileSync(p.lock, 'utf8'));
    return j && typeof j === 'object' ? j : {};
  } catch {
    return {};
  }
}

/**
 * Takes the lock. Returns {token}, or {busy: <the lock content>} when the lock file exists.
 * breakLock: delete the lock file once first. When another run creates it between the delete
 * and the create, the create fails and the result is busy.
 */
export function acquireLock(p, breakLock = false) {
  fs.mkdirSync(path.dirname(p.lock), { recursive: true, mode: 0o700 });
  if (breakLock) {
    try {
      fs.unlinkSync(p.lock);
    } catch (e) {
      if (e.code !== 'ENOENT') throw e;
    }
  }
  const token = `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const body = `${JSON.stringify({ pid: process.pid, started: new Date().toISOString(), token })}\n`;
  for (let attempt = 1; ; attempt++) {
    try {
      fs.writeFileSync(p.lock, body, { flag: 'wx', mode: 0o600 });
      return { token };
    } catch (e) {
      if (e.code === 'EEXIST') return { busy: readLock(p) };
      // ENOENT: an uninstall that ends now deleted the empty folder after its mkdir. Make it again.
      if (e.code !== 'ENOENT' || attempt >= 3) throw e;
      fs.mkdirSync(path.dirname(p.lock), { recursive: true, mode: 0o700 });
    }
  }
}

/** Deletes the lock file, only when it is still the lock of this run. */
export function releaseLock(p, token) {
  if (readLock(p).token === token) fs.rmSync(p.lock, { force: true });
}

// The host commands that run now (runFile). On SIGINT or SIGTERM under the lock, the run stops
// them and waits until they have exited before it releases the lock.
const children = new Set();
// True after SIGINT or SIGTERM under the lock: runFile then starts no new command.
let stopping = false;
const KILL_GRACE_MS = 2000;

/** Stops one host command: SIGTERM, then SIGKILL after KILL_GRACE_MS. Resolves when it has exited. */
function stopChild(child) {
  return new Promise((resolve) => {
    if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) {
      resolve();
      return;
    }
    const timer = setTimeout(() => child.kill('SIGKILL'), KILL_GRACE_MS);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill('SIGTERM');
  });
}

/** Stops each running host command (stopChild). Resolves when all have exited. */
function stopChildren() {
  return Promise.all([...children].map(stopChild));
}

/**
 * Runs fn under the lock. A second run while the lock is held exits 1 at once (no wait loop).
 * The lock is released when fn ends. On SIGINT and SIGTERM, the run stops the host command that
 * runs now, waits until it has exited, releases the lock, and then stops with that signal.
 */
async function withLock(ctx, fn) {
  const { d, o, p } = ctx;
  let lock;
  try {
    lock = acquireLock(p, o.breakLock);
  } catch (e) {
    d.err(`frontier-orchestration: cannot create the lock file ${p.lock}: ${e.message}. Nothing was changed.`);
    return 1;
  }
  if (lock.busy) {
    const b = lock.busy;
    d.err(`frontier-orchestration: the lock file ${p.lock} exists (pid ${b.pid ?? 'unknown'}, started ${b.started ?? 'unknown'}).`);
    d.err('Another install or uninstall can be running. Nothing was changed.');
    d.err('If no installer is running, run this command again with --break-lock.');
    return 1;
  }
  const onSignal = (sig) => {
    if (stopping) return;
    stopping = true;
    stopChildren().then(() => {
      releaseLock(p, lock.token);
      process.removeListener('SIGINT', onSignal);
      process.removeListener('SIGTERM', onSignal);
      process.kill(process.pid, sig);
    });
  };
  process.on('SIGINT', onSignal);
  process.on('SIGTERM', onSignal);
  try {
    return await fn();
  } finally {
    if (!stopping) {
      process.removeListener('SIGINT', onSignal);
      process.removeListener('SIGTERM', onSignal);
      releaseLock(p, lock.token);
    }
  }
}

function summary(ctx, results) {
  const { d } = ctx;
  d.out('Summary:');
  for (const [host, r] of Object.entries(results)) d.out(`  ${host.padEnd(7)} ${r.status}${r.note ? `: ${r.note}` : ''}`);
}

async function install(ctx) {
  const { d, o, version, p } = ctx;
  d.out(`frontier-orchestration ${version} installer (prototype)${o.dryRun ? ' (dry run: nothing changes)' : ''}`);
  try {
    checkBundle(ctx);
  } catch (e) {
    d.err(`frontier-orchestration: ${e.message}`);
    return 1;
  }
  const found = detect(ctx);
  const hosts = chooseHosts(ctx, found);

  d.out('[1/4] Check Python');
  const py = await checkPython(ctx);
  if (!py) return 1;

  // Steps 2 and 3 change the plugin folder and the record: they run under the lock. The lock is
  // taken before the question, so no other run can change the record or the plugin folder
  // between the plan that the user confirms and the work. A dry run changes nothing, so it takes
  // no lock.
  d.out(`Plan: copy the plugin to ${p.folder}, then register it with: ${hosts.length ? hosts.join(', ') : 'no host'}.`);
  // A run that cannot ask stops before the lock, so it creates no folder.
  if (!canConfirm(ctx)) return 2;
  const results = {};
  const copyAndRegister = async () => {
    let rec;
    try {
      rec = readRecord(p) ?? emptyRecord();
    } catch (e) {
      d.err(`frontier-orchestration: ${p.record} cannot be read: ${e.message}`);
      d.err('Nothing was changed. Repair or delete that file, then run install again.');
      return 1;
    }
    if (!(await confirm(ctx, 'Continue?'))) return d.isTTY ? 1 : 2;

    d.out(`[2/4] Copy the plugin to ${p.folder}`);
    if (o.dryRun) {
      d.out(`  (dry run) copy ${d.bundleDir} to ${p.folder} (temporary folder, then rename)`);
    } else {
      try {
        noteKept(d, installTree(d.bundleDir, p.folder, d.copyDir, (tmp) => fs.writeFileSync(path.join(tmp, MARKER), `${version}\n`)));
      } catch (e) {
        d.err(`frontier-orchestration: the copy failed, nothing was installed: ${e.message}`);
        return 1;
      }
      d.out('  done');
      try {
        saveRecord(ctx, rec);
      } catch (e) {
        d.err(`frontier-orchestration: cannot write ${p.record}: ${e.message}. No host was changed.`);
        return 1;
      }
    }

    d.out('[3/4] Register with the hosts');
    try {
      for (const host of hosts) {
        d.out(`  ${host}:`);
        results[host] = host === 'skills'
          ? installSkillsCopy(ctx, found, rec)
          : await installHostSteps(ctx, host, rec);
        if (results[host].status === 'skipped') d.out(`    skipped: ${results[host].note}`);
      }
    } catch (e) {
      // The record could not be written before or after a step. Stop: a step without a record
      // cannot be found by uninstall.
      d.err(`frontier-orchestration: cannot write ${p.record}: ${e.message}. The install stopped.`);
      summary(ctx, results);
      return 1;
    }
    return 0;
  };
  const code = o.dryRun ? await copyAndRegister() : await withLock(ctx, copyAndRegister);
  if (code !== 0) return code;

  d.out('[4/4] Set up');
  const foArgs = ['-I', path.join(p.folder, FO_REL), 'install'];
  let setupCode = 0;
  if (!o.setup) {
    d.out(`  skipped (--no-setup). Run it later: ${show(py, foArgs)}`);
  } else if (o.dryRun) {
    d.out(`  (dry run) $ ${show(py, foArgs)}`);
  } else if (!d.isTTY) {
    d.out(`  skipped: this is not a terminal. Run it in a terminal: ${show(py, foArgs)}`);
  } else {
    d.out(`$ ${show(py, foArgs)}`);
    setupCode = await d.runInherit(py, foArgs, d.env);
    if (setupCode !== 0) d.err(`  setup exit ${setupCode}. Run it again: ${show(py, foArgs)}`);
  }

  summary(ctx, results);
  const failed = Object.values(results).some((r) => r.status === 'failed');
  return failed || setupCode !== 0 ? 1 : 0;
}

/** Parses the JSON output of a list command. Returns undefined when it is not JSON. */
function parseJson(text) {
  const t = String(text).trim();
  try {
    return JSON.parse(t);
  } catch {
    // Some wrappers print a line before the JSON. Try from the first line that starts a JSON value.
    const i = t.search(/^[[{]/m);
    if (i <= 0) return undefined;
    try {
      return JSON.parse(t.slice(i));
    } catch {
      return undefined;
    }
  }
}

/** Asks the host if a step is still in place: 'present', 'absent' or 'unknown'. */
async function probeStep(ctx, exe, step) {
  const { d } = ctx;
  if (!step.probe) return 'unknown';
  d.out(`  $ ${show(exe, step.probe.args)}`);
  const r = await d.runFile(exe, step.probe.args, d.env, PROBE_TIMEOUT_MS);
  if (r.code !== 0) {
    d.out(`    cannot check: exit ${r.code}`);
    return 'unknown';
  }
  const j = parseJson(r.stdout);
  const there = j === undefined ? null : listed(step.probe.entries(j), step.probe.match);
  if (there === null) {
    d.out('    cannot check: the output is not a list that this installer knows');
    return 'unknown';
  }
  d.out(`    ${step.key}: ${there ? 'still listed' : 'not listed'}`);
  return there ? 'present' : 'absent';
}

/**
 * Runs the remove steps of one host, only for the steps in the record. Before each remove step,
 * it reads the list of the host again (ownership): the record alone never proves that this
 * installer owns what is there now. A step leaves the record only when the host shows that it is
 * gone, or that it is not ours:
 * - not listed: the step is gone (removed earlier, for example by hand). It leaves the record.
 * - from another source (the list gives another location): the step stays on the host. It does
 *   not use our folder, so it leaves the record, and the summary says so.
 * - ours: the remove command runs. Then the host lists again:
 *   - not listed: removed. It leaves the record.
 *   - still listed: the removal failed. It stays in the record.
 *   - the list cannot be read: the removal is not checked. It stays in the record.
 * - the owner cannot be found (no CLI, the list cannot be read, or it gives no location): no
 *   remove command runs. It stays in the record.
 * With --force, a step that cannot be checked leaves the record (it stays on the host as it is).
 * A step that is still listed stays in the record also with --force.
 * The marketplace step waits while the plugin step stays in the record: the plugin's owner is
 * found through its marketplace.
 * Returns {status, note, left}. left lists the steps that stay: {host, key, kind, why, cmd}, where
 * kind is 'unknown' (cannot be checked), 'listed' (still in place) or 'waits' (for the plugin).
 */
async function removeHostSteps(ctx, host, rec) {
  const { d, o } = ctx;
  const exe = which(host, d.env);
  const steps = removeSteps(host).filter((s) => stepState(rec, host, s.key));
  const notes = [];
  const left = [];
  let foreign = false;
  if (o.dryRun) {
    for (const step of steps) d.out(`  (dry run) $ ${show(exe ?? host, step.args)}`);
    return { status: 'dry run', note: '', left };
  }
  for (const step of steps) {
    const cmd = show(exe ?? host, step.args);
    if (step.key === 'marketplace' && stepState(rec, host, 'plugin')) {
      left.push({ host, key: step.key, kind: 'waits', why: 'the plugin step is still in the record', cmd });
      continue;
    }
    let state = 'unknown';
    let why;
    if (!exe) {
      why = `${host} CLI not found on PATH`;
    } else {
      const own = await ownership(ctx, exe, host, step.key);
      if (own.state === 'absent') {
        d.out(`    ${step.key}: not listed`);
        notes.push(`${step.key} was already gone`);
        dropStep(ctx, rec, host, step.key);
        continue;
      }
      if (own.state === 'foreign') {
        foreign = true;
        const msg = `${step.key} is from another source (${own.loc}); left it`;
        d.out(`    ${msg}`);
        notes.push(msg);
        dropStep(ctx, rec, host, step.key);
        continue;
      }
      if (own.state === 'unknown') {
        why = `cannot tell who owns it: ${own.why}`;
        d.out(`    ${step.key}: ${why}; no remove command runs`);
      } else {
        d.out(`  $ ${cmd}`);
        const r = await d.runFile(exe, step.args, d.env);
        const failed = r.code !== 0 ? failure(step, r) : null;
        if (failed) d.out(`    failed: ${failed}`);
        state = await probeStep(ctx, exe, step);
        if (state === 'absent') {
          dropStep(ctx, rec, host, step.key);
          continue;
        }
        const ran = failed ?? `"${step.args.slice(0, 3).join(' ')}" exit 0`;
        why = state === 'present' ? `${ran}; ${step.key} is still listed` : `${ran}; the host cannot list it now, so the removal is not checked`;
      }
    }
    if (state === 'unknown' && o.force) {
      const msg = `${step.key}: cannot check (${why}); dropped from the record (--force). If this installer added it, remove it by hand: ${cmd}`;
      d.out(`    ${msg}`);
      notes.push(msg);
      foreign = true;
      dropStep(ctx, rec, host, step.key);
      continue;
    }
    left.push({ host, key: step.key, kind: state === 'present' ? 'listed' : 'unknown', why, cmd });
  }
  if (left.length) {
    const status = left.some((l) => l.kind === 'listed') ? 'failed' : 'not checked';
    return { status, note: [...left.map((l) => `${l.key}: ${l.why}`), ...notes].join('; '), left };
  }
  return { status: foreign ? 'left' : 'ok', note: notes.join('; '), left };
}

/** The lines that say what stays after an uninstall that is not complete, and how to finish it. */
function leftMessage(ctx, left) {
  const { o, p } = ctx;
  const lines = [ctx.persist === false
    ? 'frontier-orchestration: the uninstall is not complete. These steps are not finished:'
    : `frontier-orchestration: the uninstall is not complete. These steps stay in the record (${p.record}):`];
  for (const l of left) {
    if (l.kind === 'unknown') lines.push(`  ${l.host} ${l.key}: cannot check (${l.why}). If this installer added it, remove it by hand: ${l.cmd}`);
    else if (l.kind === 'listed') lines.push(`  ${l.host} ${l.key}: still in place (${l.why}). Remove it by hand: ${l.cmd}`);
    else lines.push(`  ${l.host} ${l.key}: not tried, because ${l.why}. After the plugin is removed, remove it by hand: ${l.cmd}`);
  }
  lines.push(`${p.pluginRoot} stays${ctx.persist === false ? '' : ' with the record'}: a host can still point at it.`);
  lines.push('To finish:');
  lines.push(`  - Run "frontier-orchestration uninstall${o.force ? ' --force' : ''}" again later. It tries only the steps that are not finished.`);
  if (!o.force && left.some((l) => l.kind === 'unknown')) {
    lines.push('  - Or run "frontier-orchestration uninstall --force". It drops the steps that it cannot check from the record and');
    lines.push(`    deletes ${p.pluginRoot} when no other step stays. The host then keeps those steps as they are.`);
  }
  if (left.some((l) => l.kind === 'listed')) lines.push('  - A step that is still in place stays also with --force: remove it by hand first.');
  return lines;
}

async function uninstall(ctx) {
  const { d, o, p } = ctx;
  d.out(`frontier-orchestration uninstall (prototype)${o.dryRun ? ' (dry run: nothing changes)' : ''}`);
  // A dry run changes nothing, so it takes no lock. Every other uninstall takes the lock, also
  // when its folder does not exist yet (acquireLock creates it): an install can start and create
  // that folder while this run waits for its answer. The lock is taken before the question, so
  // the plan that the user confirms is still true when the run acts on it.
  if (o.dryRun) return uninstallLocked(ctx);
  // The folders that the lock creates now. They are deleted again at the end when they are
  // empty, so an uninstall with nothing installed leaves no folder.
  const created = [];
  for (let dir = path.dirname(p.lock); !fs.existsSync(dir) && path.dirname(dir) !== dir; dir = path.dirname(dir)) created.push(dir);
  try {
    return await withLock(ctx, () => uninstallLocked(ctx));
  } finally {
    for (const dir of created) {
      try {
        fs.rmdirSync(dir); // only an empty folder: another run can have put its lock or files in it
      } catch {
        break;
      }
    }
  }
}

/** The uninstall itself. It reads the record under the lock (see uninstall). */
async function uninstallLocked(ctx) {
  const { d, o, p } = ctx;
  let rec;
  try {
    rec = readRecord(p);
  } catch (e) {
    d.err(`frontier-orchestration: ${p.record} cannot be read: ${e.message}`);
    d.err('Nothing was changed. Repair or delete that file, then run uninstall again.');
    return 1;
  }
  const asked = o.hosts ?? HOSTS;
  // No record: a host can still point at a version folder. Never delete it without --force.
  const noRecord = rec === null;
  if (noRecord) {
    if (o.force && o.hosts) {
      // Without a record, --force deletes the plugin root folder. A host outside --hosts can
      // still point at it, so the two flags are refused together.
      d.err(`frontier-orchestration: there is no record (${p.record}), so --force cannot take --hosts.`);
      d.err('A host outside --hosts can still point at the plugin folder that --force deletes. Nothing was changed.');
      d.err('To remove the plugin from every host that is found and then delete the folder, run:');
      d.err('  frontier-orchestration uninstall --force');
      return 2;
    }
    const versions = versionFolders(p);
    if (!o.force) {
      if (versions.length) {
        d.err(`frontier-orchestration: there is no record (${p.record}), but ${p.pluginRoot} holds ${versions.join(', ')}.`);
        d.err('A host can still point at that folder, so nothing was changed.');
        d.err('To remove the plugin from every host that is found and then delete the folder, run:');
        d.err('  frontier-orchestration uninstall --force');
        return 1;
      }
      d.out(`There is no record (${p.record}) and no plugin folder. No host is changed.`);
      if (o.dryRun) {
        const items = rootItems(p);
        if (items?.foreign.length) d.out(`  (dry run) left in place: ${items.foreign.join(', ')}`);
        return 0;
      }
      if (!fs.existsSync(p.pluginRoot)) return 0;
      const kept = removePluginRoot(ctx);
      if (kept.length) {
        for (const line of keptMessage(ctx, kept)) d.err(line);
        return 1;
      }
      return 0;
    }
    // --force without a record: try each step of every host that is found.
    const found = detect(ctx);
    rec = emptyRecord();
    for (const h of asked) {
      if (!found[h]) continue;
      if (h === 'skills' && !ownedDir(p.skillsTarget)) continue;
      rec.hosts[h] = Object.fromEntries(STEP_KEYS[h].map((k) => [k, 'pending']));
    }
    ctx.persist = false;
    d.out(`There is no record (${p.record}). --force: try every host that is found.`);
  } else {
    const list = Object.entries(rec.hosts).map(([h, s]) => `${h} (${Object.entries(s).map(([k, v]) => `${k} ${v}`).join(', ')})`);
    d.out(`Registered by this installer: ${list.length ? list.join('; ') : 'no host'}`);
  }
  // Only the recorded hosts are touched. --hosts narrows that list; it never adds to it.
  const targets = asked.filter((h) => rec.hosts[h]);
  const others = Object.keys(rec.hosts).filter((h) => !targets.includes(h));
  const keepRoot = !noRecord && others.length;
  d.out(`Plan: remove the plugin from: ${targets.length ? targets.join(', ') : 'no host'}; then ${keepRoot ? `keep ${p.pluginRoot} (still registered with ${others.join(', ')})` : `delete what this installer made in ${p.pluginRoot}, then the folder when it is empty`}${targets.length ? ' when every step is finished' : ''}.`);
  d.out('Your config and your ledger are not touched.');
  if (!(await confirm(ctx, 'Continue?'))) return d.isTTY ? 1 : 2;

  const results = {};
  try {
    for (const host of asked) {
      if (!targets.includes(host)) {
        results[host] = { status: 'skipped', note: noRecord ? 'not found' : 'not registered by this installer' };
        continue;
      }
      d.out(`  ${host}:`);
      results[host] = host === 'skills' ? removeSkillsCopy(ctx, rec) : await removeHostSteps(ctx, host, rec);
    }
  } catch (e) {
    d.err(`frontier-orchestration: cannot write ${p.record}: ${e.message}. ${p.pluginRoot} stays.`);
    summary(ctx, results);
    return 1;
  }

  if (o.dryRun) {
    const foreign = keepRoot ? [] : rootItems(p)?.foreign ?? [];
    d.out(keepRoot ? `  (dry run) keep ${p.pluginRoot}` : `  (dry run) delete ${p.pluginRoot}${foreign.length ? `, except: left in place: ${foreign.join(', ')}` : ''}`);
    summary(ctx, results);
    return 0;
  }
  // The plugin root folder (with the record) stays while any step stays: a step that is not
  // finished, or a host outside --hosts.
  const left = Object.values(results).flatMap((r) => r.left ?? []);
  const remaining = Object.keys(rec.hosts);
  let kept = [];
  if (remaining.length) {
    d.out(`  kept ${p.pluginRoot}: the plugin can still be registered with ${remaining.join(', ')}.`);
  } else {
    kept = removePluginRoot(ctx);
  }
  summary(ctx, results);
  if (left.length) for (const line of leftMessage(ctx, left)) d.err(line);
  if (kept.length) for (const line of keptMessage(ctx, kept)) d.err(line);
  return left.length || kept.length ? 1 : 0;
}

function removeSkillsCopy(ctx, rec) {
  const { d, o, p } = ctx;
  let r;
  if (!fs.existsSync(p.skillsTarget)) r = { status: 'ok', note: `${p.skillsTarget} does not exist` };
  else if (!ownedDir(p.skillsTarget)) r = { status: 'skipped', note: `${p.skillsTarget} was not created by this installer; left alone` };
  else if (o.dryRun) {
    d.out(`  (dry run) delete ${p.skillsTarget}`);
    return { status: 'dry run', note: '' };
  } else {
    try {
      fs.rmSync(p.skillsTarget, { recursive: true, force: true }); // holds MARKER (checked above)
      r = { status: 'removed', note: p.skillsTarget };
    } catch (e) {
      return { status: 'failed', note: e.message, left: [{ host: 'skills', key: 'copy', kind: 'listed', why: e.message, cmd: show('rm', ['-r', p.skillsTarget]) }] };
    }
  }
  if (!o.dryRun) dropStep(ctx, rec, 'skills', 'copy');
  return r;
}

async function doctor(ctx) {
  const { d, o, p } = ctx;
  if (!fs.existsSync(path.join(p.folder, FO_REL))) {
    d.err(`frontier-orchestration: ${p.folder} is not installed. Run "frontier-orchestration install" first.`);
    return 1;
  }
  const py = await checkPython(ctx);
  if (!py) return 1;
  const args = ['-I', path.join(p.folder, FO_REL), 'doctor', ...(o.live ? ['--live'] : [])];
  if (o.dryRun) {
    d.out(`(dry run) $ ${show(py, args)}`);
    return 0;
  }
  d.out(`$ ${show(py, args)}`);
  return d.runInherit(py, args, d.env);
}
