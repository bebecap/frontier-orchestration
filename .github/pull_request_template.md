## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Evidence

<!-- The test that fails before this change and passes after it. For a measured claim, the run behind it. -->

## Checks

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `python3 -I skills/frontier-orchestration/scripts/fo.py route --check` passes
- [ ] `(cd npm && npm test)` passes, if `npm/` changed
- [ ] `claude plugin validate --strict .` passes, if a manifest, `SKILL.md` or `commands/` changed
- [ ] The version agrees in every manifest and in `fo_config.py`, if it changed
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] A new telemetry field updates `PRIVACY.md`, the allowlist test and the schema version
- [ ] No key, home path, email address or private name in the diff
