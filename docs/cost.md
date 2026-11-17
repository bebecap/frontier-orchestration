# Cost: cheap models for cheap work

> `fo.py` on this page is short for `python3 -I <skill folder>/scripts/fo.py`. See
> the top of [install.md](install.md) for the skill folder.

Most tasks in a pipeline do not need a frontier model. A format check, a log skim or a rename
can run on a small model. A frontier model is worth its price only where a mistake is expensive
or hard to detect. This skill asks the decision engine to make that call for each task.

## The policy

Every route sends a policy to the engine. The measured dataset uses this one:

> Prefer the cheapest model that does the job reliably. Spend frontier capability only where a
> mistake is expensive or hard to detect.

If your spec has no `policy`, the router sends "Prefer the cheapest model that does the job
reliably."

## The evidence

Measured on 2026-11-16 with `fo.py eval`, engine TypeSafe Jev, on the 20 labeled tasks in
[`evals/routing.json`](../skills/frontier-orchestration/evals/routing.json). The engine picked
an acceptable model for every task (pick accuracy 1.00).

| Where the pick went | Tasks |
|---|---|
| a small model (for example `haiku` or `gemini-3.8-flash`) | 5 |
| a mid model (for example `sonnet`) | 3 |
| the Jev judge itself | 4 |
| **below frontier tier, total** | **12 of 20** |
| a frontier or top model | 8 of 20 |

What the dataset labels say about these 12 tasks:

- All 9 tasks labeled `low` stakes went below frontier tier.
- 2 of the 12 are judge-only tasks. Their stakes are not labeled.
- 1 of the 12, `repo_sweep`, is labeled `high` stakes. The dataset accepts only `sonnet` (a mid
  model) for it: it is a sweep over many parallel lanes, and its citations can be checked.
- The other 8 high-stakes tasks went to frontier or top models.

So the engine saved frontier capacity on cheap work, and it did not take it away from the work
that matters.

The dataset is small. Read a difference of one or two tasks as noise.

> **No dollar amount was measured.** No percentage saving was measured either. The evidence
> above counts which tier each pick went to. It does not show a bill.

## What this can save, and what is not measured

On API billing (the usual case for teams and companies), you pay per token, and a smaller model
costs less per token. The routing evidence above shows only which tier each pick went to: all 9
`low`-stakes tasks went below frontier tier, and 8 of the 9 `high`-stakes tasks went to
frontier or top models.

That evidence does not measure a saving or the quality of the work. To know the real saving,
and whether the cheaper picks do the work well enough, measure both on the workload of your
team: the cost for each task, and a check of the result.

With engine `off`, routing makes no network call.

## How a team turns it on

1. **Choose an engine.** TypeSafe Jev had the best routing pick accuracy in the measured run
   (see [eval.md](eval.md)). OpenJev runs on your own machine. Engine `off` uses local rules:
   when its rule-based stakes are below 0.50, it picks the cheapest tier that is not the judge.

   ```bash
   fo.py setup --jev typesafe
   ```

2. **Put cheap models in `candidates`.** The router picks only from the candidates. A task
   whose candidates are all frontier models can only go to a frontier model.

3. **Set `cost_sensitivity` honestly.** For example `"high, many parallel lanes"`. The engine
   reads it as a task attribute. With engine `off`, "high" lowers the rule-based stakes by 0.10.

4. **Describe the other attributes.** `verifiable_by_code: FULLY` says that a later check
   catches a mistake. `needs_independence` and `output_kind` help the engine judge
   when a mistake is costly.

5. **Write the policy.** Put it in the spec's `policy` field. Say who acts on the result.

6. **Check, then measure.**

   ```bash
   fo.py route --check tasks.json      # no network: roster ids and the independence rule
   fo.py route tasks.json              # the decisions
   fo.py eval routing                  # the engine on the labeled dataset
   ```

7. **Use the pick.** In Claude Code, pass the alias (`haiku`, `sonnet`, `opus`, `fable`) to the
   Agent tool. For another vendor, pass the model to `fo.py ask`, for example
   `fo.py ask codex --model gpt-6-luna --prompt-file check.md`.

## What it does not do

- It does not price tokens. The roster has tiers, not prices.
- It does not make a low-stakes task free. It moves it to a cheaper tier.
- It does not lower the bar for independence. A task that needs an independent review still
  goes to a different vendor.

Next: [eval.md](eval.md).
