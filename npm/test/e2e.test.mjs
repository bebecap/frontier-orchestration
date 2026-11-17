// End to end: `npm pack` (offline, never published), unpack the tarball, run its bin with node in
// a temporary HOME with fake host CLIs, and check the result.

import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { MARKER, PLUGIN_ID } from '../lib/installer.mjs';
import { files, FO_REL, NPM_DIR, runCli, sandbox, tmpdir, VERSION } from './helpers.mjs';

function npmPath() {
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    const p = path.join(dir, 'npm');
    if (dir && fs.existsSync(p)) return p;
  }
  return null;
}

function realPythonOk() {
  const r = spawnSync('/usr/bin/python3', ['-I', '-c', 'import sys; print(sys.version_info >= (3, 9))'], { encoding: 'utf8', timeout: 30000 });
  return r.status === 0 && r.stdout.trim() === 'True';
}

test('npm pack, then the packed bin installs into a temporary HOME', { skip: npmPath() ? false : 'npm not on PATH' }, () => {
  const work = tmpdir('fo-npx-e2e-');
  const env = {
    ...process.env,
    npm_config_cache: path.join(work, 'npm-cache'),
    npm_config_offline: 'true',
    npm_config_update_notifier: 'false',
    npm_config_fund: 'false',
    npm_config_audit: 'false',
  };
  const out = execFileSync(npmPath(), ['pack', '--pack-destination', work, '--json'], { cwd: NPM_DIR, env, encoding: 'utf8' });
  const info = JSON.parse(out.slice(out.indexOf('[')))[0];
  assert.equal(info.name, 'frontier-orchestration');
  assert.equal(info.version, VERSION);
  const tgz = path.join(work, info.filename);
  const listed = execFileSync('tar', ['-tzf', tgz], { encoding: 'utf8' }).split('\n').filter(Boolean);
  for (const need of ['package/bin/cli.mjs', 'package/lib/installer.mjs', 'package/package.json', 'package/README.md',
    `package/bundle/${FO_REL}`, 'package/bundle/.claude-plugin/plugin.json', 'package/bundle/.codex-plugin/plugin.json',
    'package/bundle/.agents/plugins/marketplace.json', 'package/bundle/.antigravity-plugin/plugin.json',
    'package/bundle/PRIVACY.md', 'package/bundle/LICENSE']) {
    assert.ok(listed.includes(need), `tarball is missing ${need}`);
  }
  assert.ok(!listed.some((f) => /^package\/(test|scripts)\//.test(f) || /__pycache__|\.pyc$/.test(f)), 'tarball holds tests, build scripts or caches');
  const pkg = JSON.parse(execFileSync('tar', ['-xzOf', tgz, 'package/package.json'], { encoding: 'utf8' }));
  assert.equal(pkg.private, true, 'the package must stay private (no accidental publish)');

  const unpacked = path.join(work, 'unpacked');
  fs.mkdirSync(unpacked);
  execFileSync('tar', ['-xzf', tgz, '-C', unpacked]);
  const cli = path.join(unpacked, 'package', 'bin', 'cli.mjs');

  const real = realPythonOk();
  const sb = sandbox(real ? { python: 'real', stateful: true } : { stateful: true });
  const r = runCli(sb, ['install', '--yes', '--no-setup'], cli);
  process.stdout.write(`# e2e install output (python: ${real ? '/usr/bin/python3' : 'fake'}):\n${r.all.replace(/^/gm, '# ')}\n`);
  assert.equal(r.code, 0, r.all);
  assert.deepEqual(sb.calls(), [
    ['claude', 'plugin', 'marketplace', 'list', '--json'],
    ['claude', 'plugin', 'list', '--json'],
    ['claude', 'plugin', 'marketplace', 'add', sb.folder],
    ['claude', 'plugin', 'install', PLUGIN_ID],
    ['codex', 'plugin', 'marketplace', 'list', '--json'],
    ['codex', 'plugin', 'list', '--json'],
    ['codex', 'plugin', 'marketplace', 'add', sb.folder],
    ['codex', 'plugin', 'add', PLUGIN_ID],
    ['agy', 'plugin', 'list', '--json'],
    ['agy', 'plugin', 'install', sb.folder],
  ]);
  assert.deepEqual(files(sb.folder), [...files(path.join(unpacked, 'package', 'bundle')), MARKER].sort());
  assert.ok(fs.existsSync(path.join(sb.skillsTarget, 'SKILL.md')));

  if (real) {
    // The installed copy runs: fo.py reports the same version as the package.
    const v = execFileSync('/usr/bin/python3', ['-I', path.join(sb.folder, FO_REL), 'version'], { env: sb.env, encoding: 'utf8' });
    assert.equal(v.trim(), VERSION);
  }

  const u = runCli(sb, ['uninstall', '--yes'], cli);
  assert.equal(u.code, 0, u.all);
  assert.ok(!fs.existsSync(sb.pluginRoot));
  assert.ok(!fs.existsSync(sb.skillsTarget));
  for (const h of ['claude', 'codex', 'agy']) assert.deepEqual(sb.hostState(h), { plugins: [], marketplaces: [] }, `${h} still has the plugin`);

  // The same tarball through npx (offline, from the local file): what a user would run.
  const npx = path.join(path.dirname(npmPath()), 'npx');
  fs.writeFileSync(sb.log, '');
  const nodeDir = path.dirname(process.execPath);
  const n = spawnSync(npx, ['--yes', '--offline', `--package=${tgz}`, 'frontier-orchestration', 'install', '--yes', '--no-setup',
    '--hosts=claude,codex,agy,skills'], {
    cwd: work,
    encoding: 'utf8',
    timeout: 120000,
    // The fake CLIs come first on PATH, so a real host CLI next to node is never reached.
    env: { ...sb.env, PATH: [sb.bin, nodeDir, '/usr/bin', '/bin'].join(path.delimiter), npm_config_cache: env.npm_config_cache,
      npm_config_update_notifier: 'false', npm_config_fund: 'false', npm_config_audit: 'false' },
  });
  process.stdout.write(`# e2e npx output:\n${(n.stdout + n.stderr).replace(/^/gm, '# ')}\n`);
  assert.equal(n.status, 0, n.stdout + n.stderr);
  assert.deepEqual(sb.calls().map((c) => c.slice(0, 4)), [
    ['claude', 'plugin', 'marketplace', 'list'], ['claude', 'plugin', 'list', '--json'], ['claude', 'plugin', 'marketplace', 'add'], ['claude', 'plugin', 'install', PLUGIN_ID],
    ['codex', 'plugin', 'marketplace', 'list'], ['codex', 'plugin', 'list', '--json'], ['codex', 'plugin', 'marketplace', 'add'], ['codex', 'plugin', 'add', PLUGIN_ID],
    ['agy', 'plugin', 'list', '--json'], ['agy', 'plugin', 'install', sb.folder],
  ]);
  assert.ok(fs.existsSync(path.join(sb.folder, FO_REL)));
});
