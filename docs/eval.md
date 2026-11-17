# Evaluate the decision engine

> `fo.py` on this page is short for `python3 -I <skill folder>/scripts/fo.py`. See
> the top of [install.md](install.md) for the skill folder.

`fo.py eval` measures a decision engine on fixed datasets. Run it before you trust a new engine,
a new model id or a new OpenJev build. The code is in
[`fo_eval.py`](../skills/frontier-orchestration/scripts/fo_eval.py).

```
fo.py eval [sanity|routing|all] [--backend off|typesafe|openjev|codiv|custom] [--repeat N]
           [--json] [--out PATH] [--min-sanity 0.9] [--min-routing X]
```

## Suites

The datasets are in [`evals/`](../skills/frontier-orchestration/evals/).

| Suite | Dataset | What it measures |
|---|---|---|
| `sanity` | 24 System One questions with one clear answer: 10 Noul, 8 Choice, 6 Score | whether the engine answers simple questions correctly |
| `routing` | 20 labeled routing tasks, routed one at a time | whether the router picks an acceptable model and judges the stakes |
| `all` (default) | both | |

Engine `off` has no System One, so `sanity` is skipped for it.

## Metrics

| Metric | Suite | Meaning |
|---|---|---|
| accuracy | sanity | correct answers / all answers |
| pick accuracy | routing | picks inside the task's `acceptable` list / all picks |
| stakes accuracy | routing | stakes on the right side of 0.50 / all scored tasks |
| Brier | routing | the mean squared error of the stakes probability against the label (lower is better) |
| stakes unscored | routing | tasks with no stakes label (2 judge-only tasks); not in stakes accuracy or Brier |
| decisions | routing | the count of `act`, `ESCALATE` and `REJECT` |
| errors | both | malformed answers, for example a probability outside [0, 1], or a pick that is not a candidate |
| stability | both | with `--repeat 2` or more: the share of items that got the same answer on every run |
| p50, p95 | both | request time percentiles |

**Hard checks** (routing): no `REJECT`, and no pick from the producer's vendor where
independence is required. A failed hard check fails the run.

## Options

| Option | Meaning |
|---|---|
| `--backend B` | measure engine B instead of the configured one. The stored URL and key settings never go to another engine |
| `--repeat N` | run each suite N times (1 to 10); also reports stability |
| `--json` | print the full report as JSON |
| `--out PATH` | also write the JSON report to PATH (mode 0600) |
| `--min-sanity X` | the sanity accuracy threshold (default 0.9) |
| `--min-routing X` | the routing pick accuracy threshold (no default) |

The report holds the engine host only. It never holds a key or a full URL. Eval sends no
telemetry.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every hard check passed and every threshold was met |
| 1 | a threshold or a hard check failed |
| 2 | an error: a malformed answer, a config error, an unreachable engine, a usage error, an `--out` file that cannot be written, or nothing measured (for example `fo.py eval sanity --backend off`) |

## Examples

```bash
fo.py eval                                          # both suites, the configured engine
fo.py eval routing --backend off                    # the local heuristic baseline
fo.py eval all --repeat 2                           # the command behind the table below
fo.py eval --backend openjev --repeat 3 --json --out report.json
```

## Measured results

Source: `fo.py eval all --repeat 2`, run on 2026-11-16, and the later runs in the
[run log](#run-log). Each time is the p50 (median) per request.

| Engine | Sanity | Routing pick | Stakes | Brier | Time per request (p50) |
|---|---|---|---|---|---|
| TypeSafe Jev (hosted) | 1.000 | 1.00 | 0.889 | 0.081 | about 0.2 s |
| OpenJev, compact (mxfp4) | 1.000 | 0.85 | 0.833 | 0.107 | about 2.3 to 2.6 s sanity, 2.2 s routing |
| OpenJev, standard (4-bit) | 0.958 | 0.90 | 0.778 | 0.155 | about 0.9 s sanity, 2.3 s routing |
| `off` (local heuristic) | skipped | 0.60 | 0.778 | 0.132 | no network call |

How to read it:

- The datasets are small: 24 sanity questions and 20 routing tasks. A difference of one or two
  cases is noise.
- OpenJev times depend on the machine and on memory pressure. See
  [install.md](install.md#memory-pressure). The first build comparison on 2026-11-16 measured
  about 1.0 s sanity and 2.3 s routing for the compact build. Four later runs on the same
  machine measured 2.30 to 2.63 s sanity and 2.16 to 2.22 s routing. The table shows the later
  runs.
- Engine `off` is a baseline with no cost and no network. Compare other engines against it.

### Run log

| Date | Commit | Engine | Runs | Sanity | Pick | Stakes | Brier | Decisions (act / ESCALATE / REJECT) | p50 sanity, routing |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-16 | `f492c4e` | OpenJev, compact, 4 GB MLX cap | 3, identical | 1.000 (24/24) | 0.850 (17/20) | 0.833 | 0.107 | 13 / 7 / 0 | 2.30 to 2.45 s, 2.16 to 2.22 s |
| 2026-11-16 | `f7125bb` | OpenJev, compact, 4 GB MLX cap | 1 | 1.000 (24/24) | 0.850 (17/20) | 0.833 | 0.107 | 13 / 7 / 0 | 2.63 s, 2.20 s |
| 2026-11-16 | `f492c4e` and the fixes after it | TypeSafe Jev | 2 | 1.000 (24/24) | 1.000 (20/20) | 0.889 | 0.080 to 0.081 | 14 / 6 / 0 | 0.23 to 0.24 s, 0.22 s |

### Memory

OpenJev, compact build, physical memory from `footprint`, on 2026-11-16:

| MLX buffer cap | When | Footprint | Peak |
|---|---|---|---|
| 4 GB | loaded, before an eval | 14 GB | 15 GB |
| 4 GB | after `fo.py eval` | 21 GB | 22 GB (two runs) |
| 2 GB | after `fo.py eval` (one run) | not recorded | 20 GB |
| 1 GB | after `fo.py eval` (one run) | not recorded | 19 GB |

The eval answers were the same with each cap.

To compare OpenJev builds on a Mac, see
[`tests/e2e/compare_openjev_builds.sh`](../tests/e2e/compare_openjev_builds.sh).

Next: [providers.md](providers.md).
