#!/usr/bin/env node
// frontier-orchestration npx entry point (prototype, never published).
// This file uses top-level await, so a Node.js older than 14.8 stops with a syntax error before
// this check runs. That is accepted: such an old Node.js is rare, and its error also stops the run.
const major = Number(process.versions.node.split('.')[0]);
if (major < 18) {
  console.error(`frontier-orchestration: Node.js 18 or newer is needed (this is ${process.versions.node}).`);
  process.exit(1);
}
const { main } = await import('../lib/installer.mjs');
try {
  process.exitCode = await main(process.argv.slice(2));
} catch (e) {
  console.error(`frontier-orchestration: ${e && e.message ? e.message : e}`);
  process.exitCode = 1;
}
