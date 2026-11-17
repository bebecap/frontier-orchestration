import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { buildBundle, ITEMS } from '../scripts/bundle.mjs';
import { spawn, spawnSync } from 'node:child_process';
import { acquireLock, installTree, leftoverPattern, main, MARKER, parseArgs, PLUGIN_ID, RECORD, runFile } from '../lib/installer.mjs';
import { execFileSync, files, FO_REL, REPO_ROOT, runCli, sandbox, snapshot, stagePackage, tmpdir, VERSION } from './helpers.mjs';

const MARKETPLACE_ADD = (folder) => ['plugin', 'marketplace', 'add', folder];

/** The host calls that can change something (no python3, no read-only list call). */
function hostCalls(sb) {
  return sb.calls().filter((c) => c[0] !== 'python3' && !c.includes('list'));
}

/** Calls main() in this process with seams, for cases a subprocess cannot reach. */
async function inProcess(sb, argv, extra = {}) {
  const lines = [];
  const code = await main(argv, {
    env: sb.env,
    out: (s) => lines.push(s),
    err: (s) => lines.push(s),
    isTTY: false,
    bundleDir: stagePackage().bundle,
    version: VERSION,
    ...extra,
  });
  return { code, text: lines.join('\n') };
}

test('bundle holds the plugin layout and nothing else', () => {
  const out = path.join(tmpdir(), 'bundle');
  const r = buildBundle(REPO_ROOT, out);
  assert.equal(r.version, VERSION);
  const list = files(out);
  for (const need of [
    '.claude-plugin/plugin.json', '.claude-plugin/marketplace.json', '.codex-plugin/plugin.json',
    '.agents/plugins/marketplace.json', '.antigravity-plugin/plugin.json', 'commands/setup.md',
    'skills/frontier-orchestration/SKILL.md', FO_REL, 'skills/frontier-orchestration/scripts/fo_install.py',
    'assets/logo.svg', 'LICENSE', 'PRIVACY.md',
  ]) assert.ok(list.includes(need), `bundle is missing ${need}`);
  const tops = new Set(list.map((f) => f.split('/')[0]));
  const allowedTops = new Set([...ITEMS.map((i) => i.split('/')[0]), '.fo-bundle']);
  for (const t of tops) assert.ok(allowedTops.has(t), `unexpected top-level entry ${t}`);
  assert.ok(!list.some((f) => /(^|\/)(__pycache__|tests|node_modules)(\/|$)|\.pyc$|\.DS_Store$/.test(f)), 'bundle holds caches or tests');
  assert.ok(!list.some((f) => f.startsWith('skills/') && !f.startsWith('skills/frontier-orchestration/')));
  for (const f of ['PRIVACY.md', 'LICENSE', FO_REL]) {
    assert.deepEqual(fs.readFileSync(path.join(out, f)), fs.readFileSync(path.join(REPO_ROOT, f)), `${f} differs from the repository`);
  }
  assert.ok(!fs.readdirSync(path.dirname(out)).some((n) => n.startsWith('.bundle-tmp-')), 'temporary bundle folder left behind');
});

test('install gives each host the right argv and copies a versioned folder', () => {
  const sb = sandbox();
  const r = runCli(sb, ['install', '--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [
    ['claude', ...MARKETPLACE_ADD(sb.folder)],
    ['claude', 'plugin', 'install', PLUGIN_ID],
    ['codex', ...MARKETPLACE_ADD(sb.folder)],
    ['codex', 'plugin', 'add', PLUGIN_ID],
    ['agy', 'plugin', 'install', sb.folder],
  ]);
  // The version folder: the bundle plus the marker that says this installer made it.
  assert.deepEqual(files(sb.folder), [...files(stagePackage().bundle), MARKER].sort());
  assert.deepEqual(fs.readdirSync(sb.pluginRoot).sort(), [RECORD, VERSION].sort(), 'temporary folders left behind');
  // The list of each step is read before the first step that changes the host.
  for (const h of ['claude', 'codex']) {
    const c = sb.calls().filter((x) => x[0] === h).map((x) => x.slice(1).join(' '));
    assert.deepEqual(c.slice(0, 3), ['plugin marketplace list --json', 'plugin list --json', `plugin marketplace add ${sb.folder}`]);
  }
  assert.deepEqual(sb.calls().filter((x) => x[0] === 'agy').map((x) => x.slice(1).join(' ')), ['plugin list --json', `plugin install ${sb.folder}`]);
  assert.deepEqual(sb.recordedHosts(), ['claude', 'codex', 'agy', 'skills']);
  // skills: a copy of the skill folder plus the marker that says this installer made it.
  assert.deepEqual(files(sb.skillsTarget), [...files(path.join(sb.folder, 'skills', 'frontier-orchestration')), MARKER].sort());
  // Each command is printed before it runs.
  assert.match(r.stdout, new RegExp(`\\$ \\S*claude plugin install ${PLUGIN_ID}`));
  assert.match(r.stdout, /skipped \(--no-setup\)/);
  assert.ok(!sb.calls().some((c) => c[0] === 'python3'), 'setup ran despite --no-setup');
});

test('default hosts are only the ones that are found', () => {
  const sb = sandbox({ hosts: ['codex'], skillsDir: false });
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb).map((c) => c[0]), ['codex', 'codex']);
  assert.match(r.stdout, /Hosts: codex \(not found: claude, agy, skills\)/);
  assert.ok(!fs.existsSync(sb.skillsTarget));
});

test('--hosts limits the hosts; a requested host that is missing is skipped', () => {
  const sb = sandbox({ hosts: ['claude', 'agy'] });
  const r = runCli(sb, ['install', '--hosts=agy,codex', '--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [['agy', 'plugin', 'install', sb.folder]]);
  assert.match(r.stdout, /codex\s+skipped: codex CLI not found on PATH/);
  assert.ok(!fs.existsSync(sb.skillsTarget), 'skills was not requested');
});

test('a failing host is reported and does not stop the others', () => {
  const sb = sandbox({ fail: ['claude'] });
  const r = runCli(sb, ['install', '--yes', '--no-setup']);
  assert.equal(r.code, 1, 'a failed host makes the exit code 1');
  // claude: the marketplace add is a soft step, so the install step still runs and fails.
  assert.deepEqual(hostCalls(sb), [
    ['claude', ...MARKETPLACE_ADD(sb.folder)],
    ['claude', 'plugin', 'install', PLUGIN_ID],
    ['codex', ...MARKETPLACE_ADD(sb.folder)],
    ['codex', 'plugin', 'add', PLUGIN_ID],
    ['agy', 'plugin', 'install', sb.folder],
  ]);
  assert.match(r.stdout, /claude\s+failed: "plugin install frontier-orchestration@frontier-orchestration" exit 1: fake claude failure/);
  assert.match(r.stdout, /codex\s+ok/);
  assert.match(r.stdout, /agy\s+ok/);
  assert.match(r.stdout, /skills\s+ok/);
  assert.ok(fs.existsSync(path.join(sb.folder, FO_REL)));
});

test('--dry-run prints every step and changes nothing', () => {
  const sb = sandbox();
  const before = snapshot(sb.home);
  const r = runCli(sb, ['install', '--dry-run']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(snapshot(sb.home), before);
  assert.deepEqual(hostCalls(sb), [], 'a host CLI ran in a dry run');
  for (const want of ['(dry run) copy', `(dry run) $ ${sb.bin}/claude plugin marketplace add ${sb.folder}`,
    `(dry run) $ ${sb.bin}/agy plugin install ${sb.folder}`, '(dry run) copy', `(dry run) $ ${sb.bin}/python3 -I ${sb.folder}/${FO_REL} install`]) {
    assert.ok(r.stdout.includes(want), `missing "${want}" in:\n${r.stdout}`);
  }
  const u = runCli(sb, ['uninstall', '--dry-run']);
  assert.equal(u.code, 0, u.all);
  assert.deepEqual(snapshot(sb.home), before);
  assert.deepEqual(sb.calls().filter((c) => c[0] !== 'python3'), [], 'a host CLI (also a list) ran in a dry run');
});

test('install keeps older versions and leaves no temporary folders', () => {
  const sb = sandbox({ hosts: [] , skillsDir: false });
  const old = path.join(sb.pluginRoot, '0.0.1');
  fs.mkdirSync(old, { recursive: true });
  fs.writeFileSync(path.join(old, 'keep.txt'), 'old');
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0, 'a second install of the same version works');
  assert.deepEqual(fs.readdirSync(sb.pluginRoot).sort(), ['0.0.1', RECORD, VERSION].sort());
  assert.equal(fs.readFileSync(path.join(old, 'keep.txt'), 'utf8'), 'old');
  assert.deepEqual(files(sb.folder), [...files(stagePackage().bundle), MARKER].sort());
});

test('the copy is atomic: a failed copy installs nothing and keeps the earlier copy', async () => {
  const sb = sandbox({ hosts: ['claude'], skillsDir: false });
  const brokenCopy = (src, dst) => {
    fs.writeFileSync(path.join(dst, 'half-copied'), 'x');
    throw new Error('disk full (simulated)');
  };
  let r = await inProcess(sb, ['install', '--yes', '--no-setup'], { copyDir: brokenCopy });
  assert.equal(r.code, 1);
  assert.match(r.text, /the copy failed, nothing was installed: disk full/);
  assert.deepEqual(fs.readdirSync(sb.pluginRoot), [], 'a partial or temporary folder is left');
  assert.deepEqual(hostCalls(sb), [], 'a host was registered after a failed copy');

  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  const good = snapshot(sb.folder);
  r = await inProcess(sb, ['install', '--yes', '--no-setup'], { copyDir: brokenCopy });
  assert.equal(r.code, 1);
  assert.deepEqual(snapshot(sb.folder), good, 'the earlier copy changed');
  assert.deepEqual(fs.readdirSync(sb.pluginRoot).sort(), [RECORD, VERSION].sort());
});

test('uninstall removes the plugin from the hosts and leaves config and ledger alone', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  const config = path.join(sb.home, '.config', 'frontier-orchestration', 'config.json');
  const ledger = path.join(sb.home, '.local', 'share', 'frontier-orchestration', 'ledger', 'lessons.md');
  for (const f of [config, ledger]) {
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, 'keep me');
  }
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [
    ['claude', 'plugin', 'uninstall', PLUGIN_ID],
    ['claude', 'plugin', 'marketplace', 'remove', 'frontier-orchestration'],
    ['codex', 'plugin', 'remove', PLUGIN_ID],
    ['codex', 'plugin', 'marketplace', 'remove', 'frontier-orchestration'],
    ['agy', 'plugin', 'uninstall', 'frontier-orchestration'],
  ]);
  assert.ok(!fs.existsSync(sb.pluginRoot));
  assert.ok(!fs.existsSync(sb.skillsTarget));
  assert.equal(fs.readFileSync(config, 'utf8'), 'keep me');
  assert.equal(fs.readFileSync(ledger, 'utf8'), 'keep me');
});

test('uninstall: a failing host does not stop the rest, gives exit 1 and keeps the plugin folder', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  sb.failOn('claude', 'plugin uninstall');
  fs.writeFileSync(sb.log, '');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 1, r.all);
  // claude: the plugin removal fails, so its marketplace waits (the plugin's owner is found through it).
  assert.deepEqual(hostCalls(sb), [
    ['claude', 'plugin', 'uninstall', PLUGIN_ID],
    ['codex', 'plugin', 'remove', PLUGIN_ID],
    ['codex', 'plugin', 'marketplace', 'remove', 'frontier-orchestration'],
    ['agy', 'plugin', 'uninstall', 'frontier-orchestration'],
  ], 'every recorded host is tried');
  assert.match(r.stdout, /marketplace: the plugin step is still in the record/);
  assert.match(r.stderr, /claude marketplace: not tried, because the plugin step is still in the record/);
  assert.match(r.stdout, /claude\s+failed: .*fake claude failure/);
  assert.match(r.stdout, /codex\s+ok/);
  assert.match(r.all, /kept .*plugin.*claude/s);
  assert.ok(fs.existsSync(path.join(sb.folder, FO_REL)), 'the folder that claude still points at was deleted');
  assert.ok(!fs.existsSync(sb.skillsTarget), 'skills was removed');
  assert.deepEqual(sb.recordedHosts(), ['claude'], 'the record keeps only the host that is still registered');

  // After the fix, a second run tries only claude and then deletes the folder.
  sb.clearFaults();
  fs.writeFileSync(sb.log, '');
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb).map((c) => c[0]), ['claude', 'claude']);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('uninstall touches only the hosts that the installer recorded', () => {
  const sb = sandbox({ hosts: ['claude'], stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  assert.deepEqual(sb.recordedHosts(), ['claude', 'skills']);
  // codex and agy appear later, with the plugin from another source. Uninstall must not touch them.
  sb.setHost('codex');
  sb.setHost('agy');
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [
    ['claude', 'plugin', 'uninstall', PLUGIN_ID],
    ['claude', 'plugin', 'marketplace', 'remove', 'frontier-orchestration'],
  ]);
  assert.match(r.stdout, /codex\s+skipped: not registered by this installer/);
  assert.ok(!fs.existsSync(sb.skillsTarget));
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('uninstall without a record removes no host; --hosts narrows the record and keeps the folder', () => {
  let sb = sandbox();
  // Only items that this installer made: a leftover with the marker, an empty leftover, and a
  // temporary record file.
  const leftover = path.join(sb.pluginRoot, `.tmp-${VERSION}-AbC123`);
  fs.mkdirSync(leftover, { recursive: true });
  fs.writeFileSync(path.join(leftover, MARKER), '');
  fs.writeFileSync(path.join(leftover, 'SKILL.md'), 'stale');
  fs.mkdirSync(path.join(sb.pluginRoot, `.tmp-${VERSION}-XyZ789.old`));
  fs.writeFileSync(path.join(sb.pluginRoot, `${RECORD}.tmp-4242`), '{}');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(sb.calls(), [], 'a host was changed without a record');
  assert.match(r.all, /no record .* and no plugin folder/);
  assert.ok(!fs.existsSync(sb.pluginRoot), 'a plugin root that holds only what this installer made can go');

  sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  // A second install for one more host adds to the record; it does not replace it.
  assert.equal(runCli(sb, ['--yes', '--no-setup', '--hosts=codex']).code, 0);
  assert.deepEqual(sb.recordedHosts(), ['claude', 'codex', 'agy', 'skills']);
  fs.writeFileSync(sb.log, '');
  r = runCli(sb, ['uninstall', '--yes', '--hosts=codex']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb).map((c) => c[0]), ['codex', 'codex']);
  assert.ok(fs.existsSync(path.join(sb.folder, FO_REL)), 'the folder was deleted while other hosts still point at it');
  assert.deepEqual(sb.recordedHosts(), ['claude', 'agy', 'skills']);
});

test('a host step that fails stays in the record as pending; a dry run writes no record', () => {
  const sb = sandbox({ fail: ['agy'] });
  assert.equal(runCli(sb, ['--yes', '--no-setup', '--dry-run']).code, 0);
  assert.ok(!fs.existsSync(sb.record));
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 1);
  assert.deepEqual(sb.recordJson(), {
    hosts: {
      claude: { marketplace: 'done', plugin: 'done' },
      codex: { marketplace: 'done', plugin: 'done' },
      agy: { plugin: 'pending' },
      skills: { copy: 'done' },
    },
  });
});

test('a host CLI that reads its answer from standard input gets end of input and fails fast', async () => {
  const dir = tmpdir();
  const exe = path.join(dir, 'asks');
  fs.writeFileSync(exe, '#!/bin/sh\nif read answer; then echo "got $answer"; exit 0; fi\necho "no input" >&2\nexit 3\n', { mode: 0o755 });
  const t0 = Date.now();
  const r = await runFile(exe, [], { PATH: '/usr/bin:/bin' }, 4000);
  assert.equal(r.code, 3, JSON.stringify(r));
  assert.match(r.stderr, /no input/);
  assert.ok(Date.now() - t0 < 3000, 'the host CLI waited for input');
});

test('install removes its own leftovers from a killed run and never stages in the skills folder', async () => {
  const sb = sandbox({ hosts: [] });
  const skillsParent = path.dirname(sb.skillsTarget);
  const agents = path.dirname(skillsParent);
  // Leftovers of a killed run: temporary and set-aside copies, with a SKILL.md that agents can see.
  const stale = [
    path.join(skillsParent, '.tmp-frontier-orchestration-AbC123'),
    path.join(skillsParent, '.tmp-frontier-orchestration-AbC123.old'),
    path.join(agents, '.tmp-frontier-orchestration-XyZ789'),
    path.join(sb.pluginRoot, `.tmp-${VERSION}-QwE456`),
    path.join(sb.pluginRoot, `.tmp-${VERSION}-QwE456.old`),
  ];
  for (const s of stale) {
    fs.mkdirSync(s, { recursive: true });
    fs.writeFileSync(path.join(s, 'SKILL.md'), 'stale');
    fs.writeFileSync(path.join(s, MARKER), '');
  }
  const foreign = path.join(skillsParent, '.tmp-another-skill-1');
  fs.mkdirSync(foreign);
  // A leftover name, but no marker and not empty: the installer cannot prove that it made it.
  const unproved = path.join(sb.pluginRoot, `.tmp-${VERSION}-NoMark`);
  fs.mkdirSync(unproved);
  fs.writeFileSync(path.join(unproved, 'mine.txt'), 'mine');
  const staged = [];
  const recordingCopy = (src, dst) => {
    staged.push(dst);
    fs.cpSync(src, dst, { recursive: true });
  };
  const r = await inProcess(sb, ['--yes', '--no-setup'], { copyDir: recordingCopy });
  assert.equal(r.code, 0, r.text);
  for (const s of stale) assert.ok(!fs.existsSync(s), `leftover ${s} is still there`);
  assert.ok(fs.existsSync(foreign), 'a temporary folder of another tool was removed');
  assert.equal(fs.readFileSync(path.join(unproved, 'mine.txt'), 'utf8'), 'mine', 'a leftover without the marker was removed');
  assert.match(r.text, new RegExp(`left in place \\(no ${MARKER.replace(/\./g, '\\.')} file, not empty\\): \\S*NoMark`));
  assert.equal(staged.length, 2);
  assert.ok(staged.every((d) => !d.startsWith(skillsParent + path.sep)), `a copy was staged in the skills folder: ${staged}`);
  assert.deepEqual(fs.readdirSync(skillsParent).sort(), ['.tmp-another-skill-1', 'frontier-orchestration']);
  assert.ok(fs.existsSync(path.join(sb.skillsTarget, 'SKILL.md')));
});

test('installTree stages next to the target when the stage folder does not exist (the same-device check fails)', () => {
  const root = tmpdir();
  const src = path.join(root, 'src');
  fs.mkdirSync(src);
  fs.writeFileSync(path.join(src, 'a.txt'), 'a');
  const dest = path.join(root, 'parent', 'dest');
  const seen = [];
  installTree(src, dest, (s, d) => { seen.push(d); fs.cpSync(s, d, { recursive: true }); }, null, path.join(root, 'stage-missing'));
  assert.equal(fs.readFileSync(path.join(dest, 'a.txt'), 'utf8'), 'a');
  assert.equal(path.dirname(seen[0]), path.dirname(dest), 'a stage folder that does not exist was used');
});

test('a skills folder this installer did not create is never replaced or removed', () => {
  const sb = sandbox({ hosts: [] });
  fs.mkdirSync(sb.skillsTarget);
  fs.writeFileSync(path.join(sb.skillsTarget, 'SKILL.md'), 'mine');
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /skills\s+skipped: .* this installer did not create it; left alone/);
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(fs.readdirSync(sb.skillsTarget), ['SKILL.md']);
  assert.equal(fs.readFileSync(path.join(sb.skillsTarget, 'SKILL.md'), 'utf8'), 'mine');
});

test('refuses to run as root and changes nothing', async () => {
  const sb = sandbox();
  const before = snapshot(sb.home);
  let ran = false;
  const r = await inProcess(sb, ['install', '--yes'], { getuid: () => 0, runFile: async () => { ran = true; return { code: 0, stdout: '', stderr: '' }; } });
  assert.equal(r.code, 1);
  assert.match(r.text, /refusing to run as root/);
  assert.equal(ran, false);
  assert.deepEqual(snapshot(sb.home), before);
  for (const cmd of ['uninstall', 'doctor']) {
    assert.equal((await inProcess(sb, [cmd, '--yes'], { getuid: () => 0 })).code, 1);
  }
  assert.deepEqual(sb.calls(), []);
});

test('python3 older than 3.9 stops the install with the fix', () => {
  const sb = sandbox({ python: '3.8' });
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 1);
  assert.match(r.stderr, /python3 is 3\.8\. This skill needs 3\.9 or newer/);
  assert.match(r.stderr, /To fix:/);
  assert.ok(!fs.existsSync(sb.pluginRoot));
  assert.deepEqual(hostCalls(sb), []);
});

test('a missing python3 stops the install with the fix', () => {
  const sb = sandbox({ python: null });
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 1);
  assert.match(r.stderr, /python3 was not found on PATH/);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('setup runs fo.py install from the stable folder with the terminal attached', async () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  let inherited = null;
  const r = await inProcess(sb, ['--yes'], {
    isTTY: true,
    runInherit: async (file, args) => { inherited = [file, ...args]; return 0; },
  });
  assert.equal(r.code, 0, r.text);
  assert.deepEqual(inherited, [path.join(sb.bin, 'python3'), '-I', path.join(sb.folder, FO_REL), 'install']);
  // Not a terminal: setup is skipped and the command is printed instead.
  const c = runCli(sb, ['--yes']);
  assert.equal(c.code, 0, c.all);
  assert.match(c.stdout, /skipped: this is not a terminal/);
  assert.ok(!sb.calls().some((x) => x[0] === 'python3'));
});

test('the real interactive runner passes argv to python3 without a shell', async () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  const r = await inProcess(sb, ['--yes'], { isTTY: true });
  assert.equal(r.code, 0, r.text);
  assert.deepEqual(sb.calls(), [['python3', '-I', path.join(sb.folder, FO_REL), 'install']]);
});

test('paths with spaces and shell characters reach the hosts unchanged (no shell)', () => {
  const sb = sandbox({ homeName: 'home dir; touch PWNED $(touch PWNED2) `touch PWNED3`' });
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb)[0], ['claude', ...MARKETPLACE_ADD(sb.folder)]);
  assert.deepEqual(hostCalls(sb)[4], ['agy', 'plugin', 'install', sb.folder]);
  for (const dir of [sb.root, sb.home, process.cwd()]) {
    assert.ok(!fs.readdirSync(dir).some((n) => n.startsWith('PWNED')), `a shell ran in ${dir}`);
  }
});

test('without a terminal and without --yes, nothing changes', () => {
  const sb = sandbox();
  const before = snapshot(sb.home);
  const r = runCli(sb, ['install', '--no-setup']);
  assert.equal(r.code, 2);
  assert.match(r.stderr, /Pass --yes/);
  assert.deepEqual(snapshot(sb.home), before);
  assert.deepEqual(hostCalls(sb), []);
});

test('doctor runs fo.py doctor from the installed folder', () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  let r = runCli(sb, ['doctor']);
  assert.equal(r.code, 1);
  assert.match(r.stderr, /is not installed/);
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  r = runCli(sb, ['doctor', '--live']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(sb.calls(), [['python3', '-I', path.join(sb.folder, FO_REL), 'doctor', '--live']]);
});

test('argument parsing', () => {
  assert.equal(parseArgs([]).command, 'install');
  assert.deepEqual(parseArgs(['--hosts', 'codex,skills']).hosts, ['codex', 'skills']);
  assert.throws(() => parseArgs(['--hosts=claude,emacs']), /unknown host "emacs"/);
  assert.throws(() => parseArgs(['--hosts=']), /unknown host/);
  assert.throws(() => parseArgs(['--frobnicate']), /unknown argument/);
  assert.equal(parseArgs(['uninstall', '--force']).force, true);
  assert.throws(() => parseArgs(['install', '--force']), /uninstall flag/);
  assert.throws(() => parseArgs(['install', '--live']), /doctor flag/);
  assert.equal(parseArgs(['install', '--break-lock']).breakLock, true);
  assert.equal(parseArgs(['uninstall', '--break-lock']).breakLock, true);
  assert.throws(() => parseArgs(['doctor', '--break-lock']), /install and uninstall flag/);
  assert.throws(() => parseArgs(['uninstall', '--break-lock', '--dry-run']), /--dry-run/);
  const sb = sandbox();
  const r = runCli(sb, ['--hosts=emacs']);
  assert.equal(r.code, 2);
  assert.deepEqual(sb.calls(), []);
});

test('XDG_DATA_HOME moves the plugin folder, as fo_config.py does', () => {
  const sb = sandbox({ hosts: ['agy'], skillsDir: false });
  const xdg = path.join(sb.root, 'xdg-data');
  sb.env.XDG_DATA_HOME = xdg;
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  const folder = path.join(xdg, 'frontier-orchestration', 'plugin', VERSION);
  assert.ok(fs.existsSync(path.join(folder, FO_REL)));
  assert.deepEqual(hostCalls(sb), [['agy', 'plugin', 'install', folder]]);
});

// ---- Record per step, and an uninstall that converges (stateful fake host CLIs) ----

const NOTHING = { plugins: [], marketplaces: [] };

function assertNothingDangling(sb) {
  for (const h of ['claude', 'codex', 'agy']) assert.deepEqual(sb.hostState(h), NOTHING, `${h} still has the plugin or the marketplace`);
  assert.ok(!fs.existsSync(sb.pluginRoot), 'the plugin root folder is still there');
  assert.ok(!fs.existsSync(sb.skillsTarget), 'the skills copy is still there');
}

test('install fails at "plugin install": uninstall exits 0 and deletes the folder', () => {
  const sb = sandbox({ stateful: true });
  sb.failOn('claude', 'plugin install');
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 1, r.all);
  assert.deepEqual(sb.recordJson().hosts.claude, { marketplace: 'done', plugin: 'pending' });
  assert.deepEqual(sb.hostState('claude'), { plugins: [], marketplaces: ['frontier-orchestration'] });
  sb.clearFaults();
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  // The claude plugin step was pending: its removal failed (not installed), and the list showed it gone.
  assert.match(r.stdout, /plugin: not listed/);
  assert.match(r.stdout, /claude\s+ok: plugin was already gone/);
  assertNothingDangling(sb);
});

test('the user removed the plugin by hand: uninstall exits 0 and deletes the folder', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  // By hand: claude loses the plugin and the marketplace, agy loses the plugin.
  for (const [h, args] of [['claude', ['plugin', 'uninstall', PLUGIN_ID]], ['claude', ['plugin', 'marketplace', 'remove', 'frontier-orchestration']],
    ['agy', ['plugin', 'uninstall', 'frontier-orchestration']]]) {
    execFileSync(path.join(sb.bin, h), args, { env: sb.env });
  }
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assertNothingDangling(sb);
  // A second run has nothing to do and still exits 0.
  assert.equal(runCli(sb, ['uninstall', '--yes']).code, 0);
});

test('an install stopped between two hosts (Ctrl-C) keeps a record; uninstall removes the first host and leaves nothing dangling', async () => {
  const sb = sandbox({ stateful: true });
  sb.hangOn('codex', 'plugin marketplace add');
  const child = spawn(process.execPath, [stagePackage().cli, '--yes', '--no-setup'], { env: sb.env, stdio: ['ignore', 'pipe', 'pipe'] });
  const closed = new Promise((resolve) => child.on('close', resolve));
  const t0 = Date.now();
  while (!fs.existsSync(sb.hungPidFile) || !fs.readFileSync(sb.hungPidFile, 'utf8').trim()) {
    assert.ok(Date.now() - t0 < 30000, 'codex was never reached');
    await new Promise((r) => setTimeout(r, 50));
  }
  // The run holds the lock while it registers the hosts.
  const lockFile = path.join(path.dirname(sb.pluginRoot), '.installer.lock');
  assert.equal(JSON.parse(fs.readFileSync(lockFile, 'utf8')).pid, child.pid);
  child.kill('SIGINT');
  const [code, signal] = await new Promise((resolve) => child.on('exit', (c, s2) => resolve([c, s2])));
  await closed;
  assert.equal(signal, 'SIGINT', `the run did not stop on SIGINT (exit ${code})`);
  assert.ok(!fs.existsSync(lockFile), 'SIGINT left the lock file');
  try {
    process.kill(Number(fs.readFileSync(sb.hungPidFile, 'utf8').trim()), 'SIGKILL');
  } catch {
    // already gone
  }
  // claude is fully registered; codex started its first step; agy and skills never started.
  assert.deepEqual(sb.hostState('claude'), { plugins: [PLUGIN_ID], marketplaces: ['frontier-orchestration'] });
  assert.deepEqual(sb.recordJson(), {
    hosts: { claude: { marketplace: 'done', plugin: 'done' }, codex: { marketplace: 'pending' } },
  });
  sb.clearFaults();
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  // codex: the pending marketplace step was never added, so the list does not show it and
  // nothing is removed there.
  assert.deepEqual(hostCalls(sb), [
    ['claude', 'plugin', 'uninstall', PLUGIN_ID],
    ['claude', 'plugin', 'marketplace', 'remove', 'frontier-orchestration'],
  ], 'only the recorded steps are removed');
  assert.match(r.stdout, /codex\s+ok: marketplace was already gone/);
  assertNothingDangling(sb);
});

test('without a record, uninstall never deletes a version folder; --force tries every host found, then deletes', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  fs.rmSync(sb.record);
  const before = snapshot(sb.home);
  fs.writeFileSync(sb.log, '');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /there is no record/);
  assert.match(r.stderr, /frontier-orchestration uninstall --force/);
  assert.deepEqual(sb.calls(), [], 'a host CLI ran');
  assert.deepEqual(snapshot(sb.home), before, 'something changed');

  // codex lost the plugin by hand: --force still converges.
  execFileSync(path.join(sb.bin, 'codex'), ['plugin', 'remove', PLUGIN_ID], { env: sb.env });
  r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 0, r.all);
  assertNothingDangling(sb);
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
});

/**
 * An agy that lists the plugin (in the plugin folder of this installer) only on every second list
 * call; the other list calls fail. Its uninstall exits with uninstallExit and removes nothing.
 * Thus each uninstall run sees the plugin as ours before the removal, then cannot check it after
 * the removal.
 */
function flakyListAgy(sb, uninstallExit = 1) {
  fs.writeFileSync(path.join(sb.bin, 'agy'), [
    '#!/bin/sh',
    `{ printf '%s' agy; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "$FAKE_LOG"`,
    'n="$FAKE_STATE/agy-list-calls"',
    'case "$*" in',
    '  "plugin list"*)',
    '    c=$(( $(cat "$n" 2>/dev/null || echo 0) + 1 )); echo $c > "$n"',
    `    if [ $((c % 2)) = 1 ]; then echo '[{"name":"frontier-orchestration","path":"${sb.folder}"}]'; exit 0; fi`,
    '    echo "fake agy list failure" >&2; exit 1;;',
    `  *) echo "fake agy uninstall" >&2; exit ${uninstallExit};;`,
    'esac',
    '',
  ].join('\n'), { mode: 0o755 });
}

test('a removal that cannot be checked stays in the record and keeps the folder (exit 1); --force drops it and names the command', () => {
  for (const uninstallExit of [1, 0]) {
    // uninstallExit 0: the remove command says it worked, but the host cannot list it after.
    // That is not a verified removal either.
    const sb = sandbox({ hosts: ['agy'], skillsDir: false });
    assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
    flakyListAgy(sb, uninstallExit);
    for (let i = 1; i <= 2; i++) {
      const r = runCli(sb, ['uninstall', '--yes']);
      assert.equal(r.code, 1, r.all);
      assert.ok(fs.existsSync(sb.folder), 'the folder was deleted, but agy can still point at it');
      assert.deepEqual(sb.recordJson(), { hosts: { agy: { plugin: 'done' } } }, 'the step left the record');
      assert.match(r.stderr, /the uninstall is not complete\. These steps stay in the record/);
      assert.match(r.stderr, /agy plugin: cannot check \(.*the host cannot list it now/);
      assert.match(r.stderr, /remove it by hand: \S*agy plugin uninstall frontier-orchestration/);
      assert.match(r.stderr, /Run "frontier-orchestration uninstall" again later/);
      assert.match(r.stderr, /Or run "frontier-orchestration uninstall --force"/);
    }
    const r = runCli(sb, ['uninstall', '--yes', '--force']);
    assert.equal(r.code, 0, r.all);
    assert.match(r.stdout, /dropped from the record \(--force\)\. If this installer added it, remove it by hand: \S*agy plugin uninstall frontier-orchestration/);
    assert.ok(!fs.existsSync(sb.pluginRoot));
  }
});

test('a removal that exits 0 but the host still lists the step keeps the folder', () => {
  const sb = sandbox({ stateful: true, hosts: ['agy'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  // A broken uninstall: it exits 0 and removes nothing.
  fs.writeFileSync(path.join(sb.bin, 'agy'), fs.readFileSync(path.join(sb.bin, 'agy'), 'utf8')
    .replace('agy:plugin:uninstall:*)', 'agy:plugin:uninstall:*) exit 0;;\n  never-used)'), { mode: 0o755 });
  const r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /plugin is still listed/);
  assert.ok(fs.existsSync(sb.folder));
  assert.deepEqual(sb.recordJson().hosts, { agy: { plugin: 'done' } });
});

test('a record in the earlier format (a list of hosts) is still read', () => {
  const sb = sandbox({ stateful: true, hosts: ['agy'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  fs.writeFileSync(sb.record, JSON.stringify({ hosts: ['agy'] }));
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(sb.hostState('agy'), NOTHING);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('leftovers: only ".tmp-<name>-" plus the 6 mkdtemp characters (and ".old") match, so 0.1.0 keeps 0.1.0-beta leftovers', () => {
  const re = leftoverPattern('0.1.0');
  for (const n of ['.tmp-0.1.0-AbC123', '.tmp-0.1.0-AbC123.old']) assert.ok(re.test(n), n);
  for (const n of ['.tmp-0.1.0-beta-AbC123', '.tmp-0.1.0-beta-AbC123.old', '.tmp-0.1.0-AbC1234', '.tmp-0.1.0-AbC12', '.tmp-0x1x0-AbC123', '0.1.0']) {
    assert.ok(!re.test(n), n);
  }
  const root = tmpdir();
  const src = path.join(root, 'src');
  fs.mkdirSync(src);
  fs.writeFileSync(path.join(src, 'a.txt'), 'a');
  const parent = path.join(root, 'plugin');
  const mine = ['.tmp-0.1.0-AbC123', '.tmp-0.1.0-AbC123.old'];
  const beta = ['.tmp-0.1.0-beta-XyZ789', '.tmp-0.1.0-beta-XyZ789.old'];
  for (const n of [...mine, ...beta]) fs.mkdirSync(path.join(parent, n), { recursive: true });
  // Ownership: a leftover with the marker goes (also with files in it), an empty one goes, and
  // one with files but no marker stays.
  fs.writeFileSync(path.join(parent, mine[0], MARKER), '');
  fs.writeFileSync(path.join(parent, mine[0], 'a.txt'), 'stale');
  const unproved = '.tmp-0.1.0-QwE456';
  fs.mkdirSync(path.join(parent, unproved));
  fs.writeFileSync(path.join(parent, unproved, 'mine.txt'), 'mine');
  const kept = installTree(src, path.join(parent, '0.1.0'), (s, d) => fs.cpSync(s, d, { recursive: true }));
  assert.deepEqual(fs.readdirSync(parent).sort(), ['0.1.0', unproved, ...beta].sort());
  assert.deepEqual(kept, [path.join(parent, unproved)]);
});

test('without a record, uninstall refuses --force together with --hosts (exit 2) and changes nothing', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  fs.rmSync(sb.record);
  const before = snapshot(sb.home);
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes', '--force', '--hosts=claude']);
  assert.equal(r.code, 2, r.all);
  assert.match(r.stderr, /--hosts/);
  assert.match(r.stderr, /frontier-orchestration uninstall --force$/m);
  assert.deepEqual(sb.calls(), [], 'a host CLI ran');
  assert.deepEqual(snapshot(sb.home), before, 'something changed');
  // codex and agy still point at the folder, so it must stay.
  assert.ok(fs.existsSync(sb.folder));
});

test('a list with no entry that has a known key: the owner is unknown, so uninstall runs no remove command, keeps the record and the folder, and exits 1', () => {
  const sb = sandbox({ hosts: ['agy'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  // A list in a format this installer does not know.
  fs.writeFileSync(path.join(sb.bin, 'agy'), [
    '#!/bin/sh',
    `{ printf '%s' agy; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "$FAKE_LOG"`,
    'case "$*" in',
    '  "plugin list"*) echo \'[{"title":"frontier-orchestration"}]\';;',
    '  *) echo "fake agy failure" >&2; exit 1;;',
    'esac',
    '',
  ].join('\n'), { mode: 0o755 });
  fs.writeFileSync(sb.log, '');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /plugin: cannot tell who owns it: the output is not a list that this installer knows; no remove command runs/);
  assert.match(r.stdout, /agy\s+not checked: /);
  assert.match(r.stderr, /agy plugin: cannot check/);
  assert.deepEqual(hostCalls(sb), [], 'uninstall ran a remove command without proof of ownership');
  assert.deepEqual(sb.recordJson(), { hosts: { agy: { plugin: 'done' } } }, 'the step left the record');
  assert.ok(fs.existsSync(sb.folder), 'the folder was deleted, but agy can still point at it');
  // --force drops the step from the record, still without a remove command.
  r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [], 'uninstall --force ran a remove command without proof of ownership');
  assert.match(r.stdout, /agy\s+left: plugin: cannot check/);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('the real agy list format is read, and an agy uninstall of a missing plugin (exit 0) converges', () => {
  const sb = sandbox({ stateful: true, hosts: ['agy'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  let r = runCli(sb, ['uninstall', '--yes', '--dry-run']);
  assert.equal(r.code, 0, r.all);
  const out = execFileSync(path.join(sb.bin, 'agy'), ['plugin', 'list', '--json'], { env: sb.env, encoding: 'utf8' });
  assert.deepEqual(Object.keys(JSON.parse(out)[0]), ['name', 'enabled', 'version', 'source', 'path']);
  execFileSync(path.join(sb.bin, 'agy'), ['plugin', 'uninstall', 'frontier-orchestration'], { env: sb.env });
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /plugin: not listed/);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

// ---- Ownership: the installer adds or removes only what is in its own plugin folder ----

const GITHUB = (h) => `/fake/${h}/plugins/marketplaces/frontier-orchestration`;

/** The location that a stateful fake keeps for the marketplace of a host. */
function marketplaceLocation(sb, h) {
  return fs.readFileSync(path.join(sb.root, 'state', h, 'm', 'frontier-orchestration'), 'utf8');
}

test('install: a marketplace with this name from another source, or of unknown source, is not added or recorded; uninstall leaves it', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude', 'codex'], skillsDir: false });
  // claude: a marketplace with the same name from GitHub is in place.
  sb.addMarketplace('claude', 'frontier-orchestration', GITHUB('claude'));
  // codex: the same, but the marketplace list cannot be read, so its location cannot be found.
  sb.addMarketplace('codex', 'frontier-orchestration', GITHUB('codex'));
  sb.failOn('codex', 'plugin marketplace list');
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /claude\s+skipped: a marketplace named frontier-orchestration from another source is in place/);
  assert.match(r.stdout, /codex\s+skipped: this installer cannot tell if a marketplace named frontier-orchestration from another source is in place/);
  assert.deepEqual(hostCalls(sb), [], 'install changed a host with a foreign marketplace');
  assert.deepEqual(sb.recordJson().hosts, {}, 'a step was recorded for a marketplace this installer does not own');

  sb.clearFaults();
  fs.writeFileSync(sb.log, '');
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [], 'uninstall touched a host');
  for (const h of ['claude', 'codex']) {
    assert.deepEqual(sb.hostState(h), { plugins: [], marketplaces: ['frontier-orchestration'] });
    assert.equal(marketplaceLocation(sb, h), GITHUB(h));
  }
});

test('install: an agy plugin with this name that is not ours (no marker), or of unknown owner, is not replaced or recorded; uninstall leaves it', () => {
  const sb = sandbox({ stateful: true, hosts: ['agy'], skillsDir: false });
  // agy: the user has an own, edited frontier-orchestration plugin, without the marker file.
  const agyState = path.join(sb.root, 'state', 'agy');
  const copy = path.join(agyState, 'c', 'frontier-orchestration');
  fs.mkdirSync(copy, { recursive: true });
  fs.writeFileSync(path.join(copy, 'SKILL.md'), 'my own edited copy');
  fs.mkdirSync(path.join(agyState, 'p'), { recursive: true });
  fs.mkdirSync(path.join(agyState, 'm'), { recursive: true });
  fs.writeFileSync(path.join(agyState, 'p', 'frontier-orchestration'), '');
  const before = snapshot(agyState);
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, new RegExp(`agy\\s+skipped: a plugin named frontier-orchestration from another source is in place \\(${copy}\\)`));
  assert.match(r.stdout, /agy plugin uninstall frontier-orchestration/);
  assert.deepEqual(hostCalls(sb), [], 'install ran "agy plugin install" over a plugin that is not ours');
  assert.deepEqual(snapshot(agyState), before, 'the existing agy plugin was changed');
  assert.deepEqual(sb.recordJson().hosts, {}, 'a step was recorded for an agy plugin this installer does not own');

  // The agy list cannot be read: the owner is unknown, so the same holds.
  sb.failOn('agy', 'plugin list');
  fs.writeFileSync(sb.log, '');
  r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /agy\s+skipped: this installer cannot tell if a plugin named frontier-orchestration from another source is in place/);
  assert.deepEqual(hostCalls(sb), [], 'install ran "agy plugin install" without proof of ownership');
  assert.deepEqual(sb.recordJson().hosts, {});

  sb.clearFaults();
  fs.writeFileSync(sb.log, '');
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [], 'uninstall touched agy');
  assert.deepEqual(snapshot(agyState), before, 'uninstall changed the agy plugin of the user');
});

test('install: a claude plugin with this id whose marketplace is gone has an unknown owner: no step runs, nothing is recorded', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude'], skillsDir: false });
  // The plugin stays after its marketplace was removed; its source cannot be found.
  const claudeState = path.join(sb.root, 'state', 'claude');
  for (const sub of ['p', 'm']) fs.mkdirSync(path.join(claudeState, sub), { recursive: true });
  fs.writeFileSync(path.join(claudeState, 'p', PLUGIN_ID), '');
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /claude\s+skipped: this installer cannot tell if a plugin named frontier-orchestration@frontier-orchestration from another source is in place \(the marketplace frontier-orchestration is not listed/);
  assert.deepEqual(hostCalls(sb), [], 'install changed claude without proof of ownership');
  assert.deepEqual(sb.hostState('claude'), { plugins: [PLUGIN_ID], marketplaces: [] });
  assert.deepEqual(sb.recordJson().hosts, {});
});

test('uninstall: a pending marketplace step whose source cannot be found stays in the record and on the host; --force drops it from the record only', () => {
  // A record from an earlier installer: the marketplace step stayed pending after a failed add.
  const sb = sandbox({ stateful: true, hosts: ['codex'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup', '--hosts=codex']).code, 0);
  execFileSync(path.join(sb.bin, 'codex'), ['plugin', 'remove', PLUGIN_ID], { env: sb.env });
  execFileSync(path.join(sb.bin, 'codex'), ['plugin', 'marketplace', 'remove', 'frontier-orchestration'], { env: sb.env });
  sb.addMarketplace('codex', 'frontier-orchestration', GITHUB('codex'));
  fs.writeFileSync(sb.record, JSON.stringify({ hosts: { codex: { marketplace: 'pending' } } }));
  sb.failOn('codex', 'plugin marketplace list');
  fs.writeFileSync(sb.log, '');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /codex marketplace: cannot check \(cannot tell who owns it: .*\)\. If this installer added it, remove it by hand: \S*codex plugin marketplace remove frontier-orchestration/);
  assert.match(r.stderr, /stays with the record: a host can still point at it/);
  assert.deepEqual(hostCalls(sb), [], 'uninstall removed a marketplace by name without proof of ownership');
  assert.deepEqual(sb.recordJson(), { hosts: { codex: { marketplace: 'pending' } } });
  assert.ok(fs.existsSync(sb.folder));
  r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [], 'uninstall --force removed a marketplace by name without proof of ownership');
  assert.equal(marketplaceLocation(sb, 'codex'), GITHUB('codex'));
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('uninstall: a "done" step that the user replaced with another source is left on the host', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude', 'codex', 'agy'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  assert.deepEqual(sb.recordJson().hosts, {
    claude: { marketplace: 'done', plugin: 'done' },
    codex: { marketplace: 'done', plugin: 'done' },
    agy: { plugin: 'done' },
  });
  // By hand: claude and codex now get the plugin from a marketplace on GitHub with the same name.
  for (const [h, rm, add] of [['claude', 'uninstall', 'install'], ['codex', 'remove', 'add']]) {
    execFileSync(path.join(sb.bin, h), ['plugin', rm, PLUGIN_ID], { env: sb.env });
    execFileSync(path.join(sb.bin, h), ['plugin', 'marketplace', 'remove', 'frontier-orchestration'], { env: sb.env });
    sb.addMarketplace(h, 'frontier-orchestration', GITHUB(h));
    execFileSync(path.join(sb.bin, h), ['plugin', add, PLUGIN_ID], { env: sb.env });
  }
  // agy: a copy of the plugin from another source (it has no marker of this installer).
  const agyCopy = path.join(sb.root, 'state', 'agy', 'c', 'frontier-orchestration');
  fs.rmSync(path.join(agyCopy, MARKER), { force: true });
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(hostCalls(sb), [], 'uninstall removed a plugin or marketplace from another source');
  for (const h of ['claude', 'codex']) {
    assert.deepEqual(sb.hostState(h), { plugins: [PLUGIN_ID], marketplaces: ['frontier-orchestration'] });
    assert.equal(marketplaceLocation(sb, h), GITHUB(h));
    assert.match(r.stdout, new RegExp(`plugin is from another source \\(${GITHUB(h)}\\); left it`));
  }
  assert.deepEqual(sb.hostState('agy').plugins, ['frontier-orchestration']);
  assert.match(r.stdout, /agy\s+left: plugin is from another source/);
  assert.ok(!fs.existsSync(sb.pluginRoot), 'no host points at the plugin folder, so it goes');
});

test('uninstall: a marketplace repointed to an unmarked folder under the plugin root is foreign: no remove command runs, and the folder stays', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude', 'codex'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  // By hand: the user puts a checkout of their own (no marker) in the plugin root, and points the
  // marketplace of each host at it. Containment in the plugin root does not make it ours.
  const checkout = path.join(sb.pluginRoot, 'my-checkout');
  fs.mkdirSync(path.join(checkout, '.claude-plugin'), { recursive: true });
  fs.writeFileSync(path.join(checkout, 'notes.md'), 'my notes');
  for (const h of ['claude', 'codex']) {
    execFileSync(path.join(sb.bin, h), ['plugin', 'marketplace', 'remove', 'frontier-orchestration'], { env: sb.env });
    sb.addMarketplace(h, 'frontier-orchestration', checkout);
  }
  const before = snapshot(checkout);
  fs.writeFileSync(sb.log, '');
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.deepEqual(hostCalls(sb), [], 'uninstall removed a plugin or marketplace that points at a folder without the marker');
  for (const h of ['claude', 'codex']) {
    assert.deepEqual(sb.hostState(h), { plugins: [PLUGIN_ID], marketplaces: ['frontier-orchestration'] });
    assert.equal(marketplaceLocation(sb, h), checkout);
    assert.match(r.stdout, new RegExp(`${h}\\s+left: .*from another source \\(${checkout.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\)`));
  }
  assert.deepEqual(snapshot(checkout), before, 'uninstall changed the folder of the user');
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /left in place: \S*my-checkout/);
  assert.ok(!fs.existsSync(sb.folder), 'the version folder of this installer is not deleted');
});

test('install: a marketplace of an earlier version of this installer is ours: it is recorded, and uninstall removes it', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude'], skillsDir: false });
  // An earlier version folder of this installer holds the marker; that, not its place, makes it ours.
  const earlier = path.join(sb.pluginRoot, '0.0.1');
  fs.mkdirSync(earlier, { recursive: true });
  fs.writeFileSync(path.join(earlier, MARKER), '0.0.1\n');
  sb.addMarketplace('claude', 'frontier-orchestration', earlier);
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 0, r.all);
  // The fake refuses "add" for a name that points at another folder, so the step stays pending.
  assert.deepEqual(sb.recordJson().hosts, { claude: { marketplace: 'pending', plugin: 'done' } });
  r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(sb.hostState('claude'), NOTHING);
});

// ---- The lock: one install or uninstall at a time ----

const lockOf = (sb) => path.join(path.dirname(sb.pluginRoot), '.installer.lock');

/** Starts an install that waits in "agy plugin install" (it holds the lock), and returns it. */
async function heldByARunningInstall(sb) {
  sb.hangOn('agy', 'plugin install');
  const child = spawn(process.execPath, [stagePackage().cli, '--yes', '--no-setup'], { env: sb.env, stdio: ['ignore', 'pipe', 'pipe'] });
  const exited = new Promise((resolve) => child.on('exit', (c, sig) => resolve([c, sig])));
  const t0 = Date.now();
  while (!fs.existsSync(sb.hungPidFile) || !fs.readFileSync(sb.hungPidFile, 'utf8').trim()) {
    assert.ok(Date.now() - t0 < 30000, 'agy was never reached');
    await new Promise((r) => setTimeout(r, 50));
  }
  // Later runs must not wait in agy: only the first run is held there.
  sb.clearFaults();
  return { child, exited };
}

test('a second install or uninstall while another run holds the lock exits 1 and changes nothing', async () => {
  const sb = sandbox({ stateful: true });
  const first = await heldByARunningInstall(sb);
  try {
    const before = snapshot(sb.home);
    const record = fs.readFileSync(sb.record, 'utf8');
    fs.writeFileSync(sb.log, '');
    for (const args of [['install', '--yes', '--no-setup'], ['uninstall', '--yes'], ['uninstall', '--yes', '--force']]) {
      const t0 = Date.now();
      const r = runCli(sb, args);
      assert.equal(r.code, 1, `${args.join(' ')}: ${r.all}`);
      assert.match(r.stderr, new RegExp(`the lock file \\S+ exists \\(pid ${first.child.pid}, started \\d{4}-`));
      assert.match(r.stderr, /Nothing was changed/);
      assert.match(r.stderr, /If no installer is running, run this command again with --break-lock\./);
      assert.ok(Date.now() - t0 < 10000, 'the second run waited');
    }
    assert.deepEqual(hostCalls(sb), [], 'a second run changed a host');
    assert.deepEqual(snapshot(sb.home), before, 'a second run changed a file');
    assert.equal(fs.readFileSync(sb.record, 'utf8'), record);
  } finally {
    first.child.kill('SIGTERM');
    const [, sig] = await first.exited;
    assert.equal(sig, 'SIGTERM');
    try {
      process.kill(Number(fs.readFileSync(sb.hungPidFile, 'utf8').trim()), 'SIGKILL');
    } catch {
      // already gone
    }
  }
  assert.ok(!fs.existsSync(lockOf(sb)), 'SIGTERM left the lock file');
  // The lock is free again: an uninstall now runs.
  const r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 0, r.all);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('a lock file whose run is gone is not taken over: exit 1 names its pid and start; --break-lock deletes it once, then runs', async () => {
  const sb = sandbox({ stateful: true, hosts: ['agy'], skillsDir: false });
  fs.mkdirSync(path.dirname(lockOf(sb)), { recursive: true });
  // A run that is gone: the pid of a process that has exited.
  const gone = spawnSync('/bin/sh', ['-c', 'exit 0']).pid;
  const lock = JSON.stringify({ pid: gone, started: '2026-01-02T03:04:05.000Z', token: 'old' });
  fs.writeFileSync(lockOf(sb), lock);
  for (const args of [['--yes', '--no-setup'], ['uninstall', '--yes']]) {
    const r = runCli(sb, args);
    assert.equal(r.code, 1, r.all);
    assert.match(r.stderr, new RegExp(`exists \\(pid ${gone}, started 2026-01-02T03:04:05\\.000Z\\)`));
    assert.match(r.stderr, /If no installer is running, run this command again with --break-lock\./);
    assert.equal(fs.readFileSync(lockOf(sb), 'utf8'), lock, 'the lock file was changed');
    assert.ok(!fs.existsSync(sb.pluginRoot), 'the run went on without the lock');
  }
  // A lock file that is not JSON: the message says that pid and start time are unknown.
  fs.writeFileSync(lockOf(sb), 'garbage');
  let r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /exists \(pid unknown, started unknown\)/);
  // --break-lock: the lock file goes, the run takes the lock, and releases it at the end.
  r = runCli(sb, ['--yes', '--no-setup', '--break-lock']);
  assert.equal(r.code, 0, r.all);
  assert.ok(fs.existsSync(sb.folder));
  assert.ok(!fs.existsSync(lockOf(sb)), 'the lock was not released');
  fs.writeFileSync(lockOf(sb), lock);
  r = runCli(sb, ['uninstall', '--yes', '--break-lock']);
  assert.equal(r.code, 0, r.all);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

test('--break-lock deletes the lock file once: when another run creates it again first, this run exits 1', (t) => {
  const root = tmpdir();
  const p = { lock: path.join(root, 'share', '.installer.lock') };
  fs.mkdirSync(path.dirname(p.lock), { recursive: true });
  fs.writeFileSync(p.lock, JSON.stringify({ pid: 1, started: 'old', token: 'old' }));
  const realUnlink = fs.unlinkSync;
  let unlinks = 0;
  t.mock.method(fs, 'unlinkSync', (f) => {
    unlinks++;
    realUnlink(f);
    // Another run takes the lock between the delete and the create.
    fs.writeFileSync(f, JSON.stringify({ pid: 2, started: 'other', token: 'other' }), { flag: 'wx' });
  });
  const lock = acquireLock(p, true);
  assert.equal(unlinks, 1, 'the lock file was not deleted exactly once');
  assert.deepEqual(lock, { busy: { pid: 2, started: 'other', token: 'other' } });
  assert.equal(JSON.parse(fs.readFileSync(p.lock, 'utf8')).token, 'other', 'the lock of the other run was changed');
  // Without --break-lock, nothing is deleted.
  assert.deepEqual(acquireLock(p), { busy: { pid: 2, started: 'other', token: 'other' } });
  assert.equal(unlinks, 1);
});

test('SIGTERM under the lock stops the host command first: the lock goes only after it is dead, and its late work never happens', async () => {
  const sb = sandbox({ hosts: ['agy'], skillsDir: false });
  const pidFile = path.join(sb.root, 'state', 'slow.pid');
  const marker = path.join(sb.root, 'state', 'late-marker');
  // A slow agy: it ignores SIGTERM and SIGINT, and writes a marker after 4 s.
  fs.writeFileSync(path.join(sb.bin, 'agy'), [
    '#!/bin/sh',
    'case "$*" in',
    '  "plugin list"*) echo "[]"; exit 0;;',
    `  "plugin install"*) trap '' TERM INT; echo $$ > "${pidFile}"; sleep 4; touch "${marker}"; exit 0;;`,
    'esac',
    'exit 0',
    '',
  ].join('\n'), { mode: 0o755 });
  const child = spawn(process.execPath, [stagePackage().cli, '--yes', '--no-setup'], { env: sb.env, stdio: ['ignore', 'pipe', 'pipe'] });
  const exited = new Promise((resolve) => child.on('exit', (c, sig) => resolve([c, sig])));
  let done = null;
  exited.then((v) => { done = v; });
  const t0 = Date.now();
  while (!fs.existsSync(pidFile) || !fs.readFileSync(pidFile, 'utf8').trim()) {
    assert.ok(Date.now() - t0 < 30000, 'agy was never reached');
    await new Promise((r) => setTimeout(r, 20));
  }
  const slowStart = Date.now();
  const slow = Number(fs.readFileSync(pidFile, 'utf8').trim());
  const alive = (pid) => {
    try {
      process.kill(pid, 0);
      return true;
    } catch (e) {
      return e.code === 'EPERM';
    }
  };
  child.kill('SIGTERM');
  let seenAliveWithLock = 0;
  while (!done) {
    if (alive(slow)) {
      assert.ok(fs.existsSync(lockOf(sb)), 'the lock was released while the host command still ran');
      seenAliveWithLock++;
    }
    assert.ok(Date.now() - slowStart < 20000, 'the run did not stop');
    await new Promise((r) => setTimeout(r, 10));
  }
  assert.equal(done[1], 'SIGTERM', `the run did not stop with SIGTERM (exit ${done[0]})`);
  assert.ok(seenAliveWithLock > 0, 'the host command ignores SIGTERM, so it must live until SIGKILL');
  assert.ok(!alive(slow), 'the host command still runs after the run stopped');
  assert.ok(!fs.existsSync(lockOf(sb)), 'SIGTERM left the lock file');
  // Wait past the 4 s of the slow agy: its late marker must never appear.
  await new Promise((r) => setTimeout(r, Math.max(0, slowStart + 5000 - Date.now())));
  assert.ok(!fs.existsSync(marker), 'the host command went on after the run stopped');
});

test('without a record, --force keeps the plugin folder while a removal fails and the host still lists the step', () => {
  const sb = sandbox({ stateful: true, hosts: ['claude'], skillsDir: false });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  fs.rmSync(sb.record);
  sb.failOn('claude', 'plugin uninstall');
  let r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /the uninstall is not complete\. These steps are not finished:/);
  assert.match(r.stderr, /claude plugin: still in place/);
  assert.ok(fs.existsSync(sb.folder), 'the folder was deleted, but claude still points at it');
  sb.clearFaults();
  r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 0, r.all);
  assert.ok(!fs.existsSync(sb.pluginRoot));
});

// ---- Review round 29: the lock is taken before the question; a host command that ignores SIGTERM ----

test('uninstall --force with no state folder takes the lock before it asks: another run cannot take it, and nothing it made is deleted', async () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  const stateDir = path.dirname(sb.pluginRoot);
  assert.ok(!fs.existsSync(stateDir), 'the sandbox must start without the state folder');
  let other = null;
  const r = await inProcess(sb, ['uninstall', '--force'], {
    isTTY: true,
    // While the question waits for an answer, another install starts: it takes the lock and
    // copies its plugin folder (when it gets the lock).
    ask: async () => {
      other = acquireLock({ lock: lockOf(sb) });
      if (other.token) {
        fs.mkdirSync(sb.folder, { recursive: true });
        fs.writeFileSync(path.join(sb.folder, MARKER), `${VERSION}\n`);
      }
      return 'y';
    },
  });
  assert.ok(other, 'the uninstall did not ask');
  if (other.token) assert.ok(fs.existsSync(sb.folder), 'the uninstall deleted the plugin folder of a run that holds the lock');
  assert.ok(other.busy, 'another run took the lock while the uninstall waited for its answer');
  assert.equal(r.code, 0, r.text);
  assert.ok(!fs.existsSync(lockOf(sb)), 'the lock was not released');
  assert.ok(!fs.existsSync(stateDir), 'the uninstall left the folder that it made for the lock');
});

test('install takes the lock before it asks', async () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  let other = null;
  const r = await inProcess(sb, ['install', '--no-setup'], {
    isTTY: true,
    ask: async () => {
      other = acquireLock({ lock: lockOf(sb) });
      return 'n';
    },
  });
  assert.ok(other, 'the install did not ask');
  assert.ok(other.busy, 'another run took the lock while the install waited for its answer');
  assert.equal(r.code, 1, r.text);
  assert.ok(!fs.existsSync(lockOf(sb)), 'the lock was not released');
  assert.ok(!fs.existsSync(sb.pluginRoot), 'the answer was no, but the plugin was copied');
});

test('runFile at the timeout stops a host command that ignores SIGTERM (SIGKILL) and returns a timeout failure, never success', async () => {
  const dir = tmpdir();
  const pidFile = path.join(dir, 'pid');
  const alive = (pid) => {
    try {
      process.kill(pid, 0);
      return true;
    } catch (e) {
      return e.code === 'EPERM';
    }
  };
  // 'ignores': SIGTERM does nothing. 'exits0': SIGTERM makes it exit 0.
  for (const [name, trap] of [['ignores', "trap '' TERM"], ['exits0', "trap 'exit 0' TERM"]]) {
    const exe = path.join(dir, name);
    fs.writeFileSync(exe, `#!/bin/sh\n${trap}\necho $$ > "${pidFile}"\nwhile :; do sleep 1; done\n`, { mode: 0o755 });
    fs.rmSync(pidFile, { force: true });
    const t0 = Date.now();
    let limit;
    const r = await Promise.race([
      runFile(exe, [], { PATH: '/usr/bin:/bin' }, 1000),
      new Promise((resolve) => { limit = setTimeout(() => resolve(null), 15000); }),
    ]);
    clearTimeout(limit);
    const pid = Number(fs.readFileSync(pidFile, 'utf8').trim());
    try {
      assert.ok(r, `${name}: runFile did not return after the timeout`);
      assert.notEqual(r.code, 0, `${name}: a timeout gave success: ${JSON.stringify(r)}`);
      assert.match(r.stderr, /stopped after 1 s \(timeout\)/, name);
      assert.ok(!alive(pid), `${name}: the host command still runs`);
      assert.ok(Date.now() - t0 < 10000, `${name}: runFile took too long`);
    } finally {
      try {
        process.kill(pid, 'SIGKILL');
      } catch {
        // already gone
      }
    }
  }
});

// ---- Review round 30: the installer deletes recursively only what it can prove it owns ----

/**
 * Runs fn while fs.rmSync records each recursive delete. A delete "breaks the rule" when its
 * target exists and is not a real folder that holds MARKER at that moment.
 */
async function withDeleteSpy(fn) {
  const real = fs.rmSync;
  const broken = [];
  fs.rmSync = (target, opts) => {
    if (opts && opts.recursive) {
      let st = null;
      try {
        st = fs.lstatSync(target);
      } catch {
        // gone: nothing to break
      }
      if (st && !(st.isDirectory() && fs.existsSync(path.join(String(target), MARKER)))) broken.push(String(target));
    }
    return real.call(fs, target, opts);
  };
  try {
    return { result: await fn(), broken };
  } finally {
    fs.rmSync = real;
  }
}

test('uninstall without a record keeps a folder the user put in the plugin root: nothing is deleted, exit 1, also with --force', () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  const checkout = path.join(sb.pluginRoot, 'my-checkout');
  fs.mkdirSync(path.join(checkout, '.git'), { recursive: true });
  fs.writeFileSync(path.join(checkout, '.git', 'HEAD'), 'ref: refs/heads/main\n');
  fs.writeFileSync(path.join(checkout, 'notes.md'), 'my notes');
  const before = snapshot(sb.pluginRoot);
  for (const args of [['uninstall', '--yes'], ['uninstall', '--yes', '--force']]) {
    const r = runCli(sb, args);
    assert.equal(r.code, 1, `${args.join(' ')}: ${r.all}`);
    assert.match(r.stderr, /left in place: \S*my-checkout/);
    assert.deepEqual(snapshot(sb.pluginRoot), before, `${args.join(' ')} changed the plugin root`);
  }
});

test('uninstall deletes its own version folder and the record, but keeps a foreign file and the plugin root (exit 1), also with --force', () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  const foreign = path.join(sb.pluginRoot, 'notes.txt');
  fs.writeFileSync(foreign, 'mine');
  let r = runCli(sb, ['uninstall', '--yes']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /left in place: \S*notes\.txt/);
  for (const h of ['claude', 'codex', 'agy']) assert.deepEqual(sb.hostState(h), NOTHING, `${h} still has the plugin`);
  assert.ok(!fs.existsSync(sb.folder), 'the owned version folder is still there');
  assert.ok(!fs.existsSync(sb.record), 'the record is still there');
  assert.ok(!fs.existsSync(sb.skillsTarget), 'the skills copy is still there');
  assert.deepEqual(fs.readdirSync(sb.pluginRoot), ['notes.txt']);
  assert.equal(fs.readFileSync(foreign, 'utf8'), 'mine');
  r = runCli(sb, ['uninstall', '--yes', '--force']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /left in place: \S*notes\.txt/);
  assert.equal(fs.readFileSync(foreign, 'utf8'), 'mine');
});

test('a normal install, then uninstall: the plugin root goes (empty-folder removal), exit 0, and every recursive delete hits a folder with the marker', async () => {
  const sb = sandbox({ stateful: true });
  assert.equal(runCli(sb, ['--yes', '--no-setup']).code, 0);
  const { result: r, broken } = await withDeleteSpy(() => inProcess(sb, ['uninstall', '--yes']));
  assert.equal(r.code, 0, r.text);
  assert.deepEqual(broken, [], 'a recursive delete hit a folder without the marker');
  assertNothingDangling(sb);
});

test('install never replaces a version folder that it did not create (no marker): exit 1, the folder stays as it was', () => {
  const sb = sandbox({ hosts: [], skillsDir: false });
  fs.mkdirSync(sb.folder, { recursive: true });
  fs.writeFileSync(path.join(sb.folder, 'mine.txt'), 'mine');
  const before = snapshot(sb.pluginRoot);
  const r = runCli(sb, ['--yes', '--no-setup']);
  assert.equal(r.code, 1, r.all);
  assert.match(r.stderr, /this installer did not create it/);
  assert.deepEqual(snapshot(sb.pluginRoot), before);
});
