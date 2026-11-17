# Contributing

Thanks for helping. Keep changes small and evidence-backed.

## Ground rules

- Standard library only. Python 3.9+, macOS and Linux.
- Every behavior change comes with a test that fails before the change and passes after it.
  Break the code on purpose once to prove the test can go red.
- Network paths are tested against real local HTTP servers (`tests/helpers.py`), not mocks.
  `unittest.mock` is used for subprocess calls, installer steps, terminal input and some error
  cases. One test patches `urllib.request.build_opener` only to prove that the telemetry sender
  never opens a connection to a lookalike host.
- A new model in `data/roster.json` needs a vendor, a tier and a description that says what the
  model is for. A measured claim in a description needs the run behind it in the PR text.
- A new rule in `references/rules.md` needs two or more distinct runs that confirm it.
- Never add a telemetry field without updating `PRIVACY.md`, the allowlist test, the schema
  version and `CHANGELOG.md`.

## Check before a PR

```bash
python3 -m unittest discover -s tests -v
python3 -I skills/frontier-orchestration/scripts/fo.py route --check
(cd npm && npm test)                    # if npm/ changed
(cd collector && node --test --experimental-test-module-mocks)   # if collector/ changed (Node 22)
bash tests/e2e/smoke.sh                 # a new user's first commands, engine off, no network
claude plugin validate --strict .       # if you have Claude Code
```

## Release

1. Bump `version` in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
   `.codex-plugin/plugin.json`, `.antigravity-plugin/plugin.json`, `npm/package.json` and
   `VERSION` in `skills/frontier-orchestration/scripts/fo_config.py` (a test checks they agree).
2. Add a `CHANGELOG.md` entry.
3. `claude plugin tag . --push` creates and pushes `frontier-orchestration--v<version>`.

### Before publishing

Run the history check before you push to a public remote or make the repository public:

```bash
python3 -I scripts/prepublish_check.py            # --ref REF, --repo PATH, --denylist PATH
```

The owner may publish under one chosen address; pass it explicitly with `--allow-email ADDRESS`
(repeat it for each address). It is an exact match that ignores ASCII case only: no wildcard,
no pattern, and no other address on the same domain. A value that is not one plain ASCII
address is refused (exit 2). It allows the address only as an author, committer or tagger
email; in a message or a file it is still an email address. Without the option, only noreply
identities pass.

It reads every commit reachable from the ref (default `HEAD`), not only the last one, every tag
that points into it and every notes ref. It fails when an author, committer or tagger email is not
a GitHub noreply address, the Anthropic noreply address or an `--allow-email` address, when a
commit or tag message has a `Claude-Session:` trailer, and when a message, an identity name, a ref
name (each branch, tag, notes ref or other ref that points into the history), any path or any file
in any commit has a deny-listed word, a home path, an email address or a key. Every path is checked
(also both sides of a rename and deleted files), and content is scanned as bytes, so a file that is
not UTF-8 is still checked. Deny-listed words match in any case and in Unicode variants: fullwidth
and other compatibility forms, accents and every other mark, zero-width and other invisible
characters (soft hyphens, variation selectors, bidi controls, tag characters), and Cyrillic and
Greek look-alikes. It also fails if `.privacy-denylist` was ever committed, on a submodule or a
regular file that is a Git LFS pointer, whose content it cannot see, and on a regular file (not a
symlink) that is an archive (`.zip`, `.gz`, `.tar`, `.tgz`, `.7z` and others, found by name or by
its first bytes): look at the archive, then pass `--allow-archive PATH` for it. It lists every file
whose content it cannot read as text under "binary files, review by eye", with the reason: a file
with NUL bytes, or one that its first bytes or its extension show to be a PDF, PostScript, image,
font, audio, video, SQLite database, pickle, compiled object, disk image or another format in the
script's lists (a PDF with compressed text has no NUL bytes, so `%PDF-` or `.pdf` finds it). Their
bytes are scanned, but compressed content is not; look at each one. A .svg, .html, .htm, .xhtml,
.md or .markdown file is scanned as text, and is listed too if it holds a `data:...;base64,`
payload of 200 characters or more. Office files (docx, xlsx, pptx, odt) are zip archives, so they
fail as archives.

Every rule that depends on a file's path, extension or mode (the review list, archives,
`--allow-archive`, LFS pointers, submodules, the deny list file, the files whose content is not
scanned) is checked for every file in every commit. Two files with the same bytes share only what
the bytes alone show; each gets the result it would get alone. Each line is printed once per
path, at the first commit that has it.

It stops without a verdict (exit 2) on a shallow clone, a partial clone, a grafts file or an
object it cannot read, and it ignores replace refs. It needs your local `.privacy-denylist` and
stops if it cannot find it, unless you pass `--no-denylist` (not a full release check). The
output names the commit and the rule, never the matched text.

Known limits:

- It is a check for known patterns: the words in your deny list, home paths, email addresses and
  key formats. A pass is not proof that the history holds nothing private.
- The content of `tests/test_package.py` and `tests/test_telemetry_ledger.py` (`ALLOW` in the
  script) is not scanned, because they hold the patterns and their fixtures. The content of a
  committed `.privacy-denylist` is not scanned either (the gate fails on it anyway). Their paths
  are.
- Compressed content is not unpacked: archives fail until you allow them by path, and the other formats
  it cannot read (PDF, images, fonts, audio, video and the rest of the list above) are only listed
  for review by eye. A listed file does not fail the gate.
- It checks the history of one ref, its tags and the notes refs. Other branches, and tags that
  point outside that history, are not checked: push the ref and its tags by name, never with
  `--all`, `--mirror` or `--tags`.

If it fails, do not publish that history. Publish a single-commit snapshot of the tree instead.
