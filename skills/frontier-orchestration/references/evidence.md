# Evidence: the measured runs behind the rules

These numbers come from the maintainer's own runs. They are priors about that work, not
benchmarks. Re-measure on yours and record the result in `reviewer-priors.md`.

## Routing confidence (5 tasks, one pipeline)

| | v1: every model as an option | v2: pruned to plausible candidates |
|---|---|---|
| mean routing confidence | 0.770 | 0.740 |
| minimum | 0.57 | 0.42 |

Pruning made confidence fall while routing got better. Two routes collapsed to near-ties
(`estimate_review` 0.79 -> 0.48, `arithmetic_audit` 0.88 -> 0.42) because every surviving
candidate was defensible. The same `estimate_review` route returned 0.48, then 0.29 on a re-run.
Stakes stayed at 0.89 and 0.85, so the gate escalated both times.

Asked where a routing decision must sit before code acts without escalating, Jev scored 1.83 of
2: "act above roughly 0.8".

## Independence

Asked whether a review may come from the producer's own vendor, Jev answered 0.26 (no). Asked
whether a second opinion is worth paying for on work a customer will budget against: 0.85. Asked
how large the shared-blind-spot risk is when one family produces and reviews: 1.85 of 2.

Asked to rank itself, one frontier model declined: it could not substantiate a strongest-task
ranking. Treat every model's self-assessment that way.

## Two reviewers, one packet (estimate review)

Both reviewers got the identical packet and never saw each other's answer. Each claim was then
checked against the repository.

| | `gpt-5.6-sol` xhigh | `gpt-6-astra` xhigh |
|---|---|---|
| specific claims that held | framing held; no false file:line | 3 of 5 (60%) |
| what it found | a missing **category**: a whole release package nobody priced | a concrete **defect**: an account-scoped data leak |
| sizing | its ask held on verification | asked +3.5 to 5 days; verified worth +1 to 2 |

- The two models failed in different directions, not at different levels. Neither found the
  other's class of problem. Running both on the one escalated decision gave the complete answer.
- They disagreed about the biggest item. Both were partly right, and the code settled it, not a
  third opinion.
- A true finding carried a size about 2.5x its verified size. Forwarded unverified, it would have
  inflated the estimate by about three days.
- One verified claim came back stronger than stated: the code had a cleanup function whose own
  comment stated the invariant, and nothing called it.

## Four Codex reviewers, one seeded file (2026-11-17)

One Python file of 342 lines with 10 seeded defects, one per class (race, path traversal,
off-by-one, resource leak, swallowed exception, missing timeout, non-atomic write, time units,
aliasing, allow-list bypass), and 4 traps: code that looks wrong and is correct. A script proves
each defect and each trap by execution. Each reviewer got the same instructions through
`fo.py ask codex --effort high`, saw no key, and ran once. Every claim outside the key was then
checked by execution.

| | `gpt-6.2-sol` | `gpt-6.2-astra` | `gpt-6.1-sol` | `gpt-6-astra` |
|---|---|---|---|---|
| findings | 21 | 20 | 15 | 16 |
| seeded defects found | 10 of 10 | 10 of 10 | 9 of 10 | 10 of 10 |
| traps flagged as defects | 0 | 0 | 0 | 0 |
| true defects outside the key | 7 | 8 | 5 | 5 |
| false claims | 4 | 2 | 1 | 1 |
| claims that held | 17 of 21 | 18 of 20 | 14 of 15 | 15 of 16 |
| time | 120 s | 132 s | 173 s | 98 s |

- The seeded defects separate the reviewers very little: three of four found all 10, and nobody
  flagged a trap. The difference is in the extras: the two new models reported more, found 2 or 3
  more real defects, and made more false claims.
- `gpt-6.1-sol` missed the swallowed exception as a finding of its own; it named the same effect
  inside another finding, which was not counted.
- Two of the false claims of `gpt-6.2-sol` need a nonsense input (a negative byte limit, a timeout of
  `nan`) after which the code fails safely. Counted as defects, its result is 19 of 21.
- Limits: one run per model, one synthetic file, no variance. This is a prior about seeded
  defects in a small file, not about a real review of a large change.

## Feeding priors back into the roster

Rewriting the roster descriptions to say what each reviewer finds, its hit rate and its sizing
bias moved the same route from 0.48 (escalate) to 0.92 (act, on the reviewer that verification
had vindicated). Higher confidence after an update is not proof of a better answer; here it
agreed with the verified outcome. Every escalation marks a missing prior: measure it, write it
into the roster, and that decision routes cheaply from then on.

## Later runs, distilled

- Write the pricing conventions into the sweep prompt before the sweep. Two careful sweeps of the
  same code differed about 1.6x, mostly on conventions.
- On an already-verified estimate, reviewer sizes shrink and their framing does not. Whatever both
  reviewers raise independently is real in direction; verify the size.
- Judge panels drift toward the minimalist. Tell the synthesizer to take the lens the code
  supports, and ask the cross-vendor review for an explicit low-bias check.
- Run two critic rounds on different models, then a ledger audit, after any per-item step. The
  second round found the top integration gap that every earlier lane missed.
- Packets for a reviewer have size limits: send an index that points to evidence files.
- Jev Score is stable run to run; Choice is not. Sample Choice more than once.

## Gemini as a reviewer (`gemini-4-argon`, content and visual work only)

Screenshot reading: 42 of 63 claims held, 5 refuted. Visual defects: 10 of 10 held. In a spec
review the low-severity findings held 7 of 7, and one medium finding was invented: it quoted
figures that the artefact does not contain. No measured prior for code review.

## Sources

- [Capability Frontier](https://arxiv.org/html/2606.26836v1): routing across models with
  complementary errors beats the best single model; correcting for single-model evaluation and
  run variability cuts error.
- [LLM routing with benchmark datasets](https://arxiv.org/pdf/2309.15789)
- [TypeSafe: confidence](https://docs.typesafe.ai/confidence.md): confidence measures
  concentration, not correctness.
