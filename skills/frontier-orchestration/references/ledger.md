# The learning ledger

A plain folder of Markdown notes (Obsidian-compatible). Default location:
`~/.local/share/frontier-orchestration/ledger`. Put it in a vault with
`fo.py config set ledger.path ~/Vault/learning/frontier-orchestration` and, to write the activity
log into the vault's own log, `fo.py config set ledger.log ~/Vault/log.md` (that file must exist).
Both must be full paths (they start with `/` or `~`); a relative path is a config error.

```
<ledger>/
    README.md                   hub
    reviewer-priors.md          measured hit rates per model x artefact kind (fill by hand)
    lessons/fo-L###-<slug>.md   one lesson: rule, why, how to apply, evidence, counter-evidence
    runs/fo-run-<id>.md         one run: routing, rounds table, retro answers, lessons touched
    log.md                      newest-first activity log
```

## Commands

```
fo.py lessons init
fo.py lessons recall [--tags a,b] [--kind k] [--all] [--limit N]
fo.py lessons run-new --id YYYY-MM-DD-project-name --project SLUG \
                      --artefact estimate-raw|estimate-verified|code|schedule|content|other --title "..."
fo.py lessons add --slug s --rule "<one imperative sentence>" --why "..." --kind KIND \
                  --tags a,b --run RUN --evidence "..." [--source PATH] [--apply "trigger -> action"]
fo.py lessons reinforce FO-L007 --run RUN --evidence "what happened" [--source PATH]
fo.py lessons contradict FO-L007 --run RUN --evidence "what broke it" [--source PATH]
fo.py lessons retire FO-L007 --reason "..."
fo.py lessons run-close RUN
fo.py lessons lint
```

Kinds: routing, reviewer-prior, review-loop, fix-design, verification, estimating, scheduling,
harness, release.

- `recall` without `--tags` prints confirmed lessons, then disputed ones (flagged "re-check");
  `--all` adds candidates. With `--tags` it includes matching candidates.
- `add` refuses a rule that looks like an existing one: reinforce that one instead.
- `--source` must be a durable path (the ledger or a repository), never a scratch folder.
- Free text is folded to one line, so it cannot create a heading or a fake frontmatter key.
- Every write is atomic under one folder lock.

## Status (computed; never hand-edit it)

| Status | Meaning | Do |
|---|---|---|
| candidate | seen in 1 run | use it as a hint; look for a second run |
| confirmed | 2 or more distinct runs, fewer contradict it | apply it by default |
| disputed | at least as many runs contradict it as confirm it | re-check before use; narrow or retire it |
| retired | `retire` | kept for history; never recalled |

A run counts once, for its latest verdict on a lesson.

## Promote

A confirmed lesson that changes **which model** to use goes into your roster override
(`~/.config/frontier-orchestration/roster.json`): rewrite that model's description to say what it
finds, its hit rate and its sizing bias. A confirmed lesson that changes **how** to run goes into
your prompts and plans. Keep run narratives in the ledger, not in the skill.

## What never goes in the ledger

Project facts (they belong with the project), secrets, tokens, emails, customer content.
A lesson is about how to orchestrate models. No evidence, no lesson.
