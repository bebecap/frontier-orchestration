# Pipelines

## Estimate pipeline

```
 RECALL  fo.py lessons recall + reviewer priors; put what applies into prompts
                                          |
 fo.py refresh: reachable models --> fo.py route: Jev routes each task (choice + stakes)
                                          |
                        +-----------------+------------------+
                        |                                    |
        confidence >= 0.8  or  stakes < 0.5          escalated decision
                 act on the route                head-to-head Nouls or an ensemble
                        |                                    |
                        +-----------------+------------------+
                                          |
              CONVENTIONS  write the pricing rules into every prompt first
              PRODUCE      fan out on non-overlapping lanes; every claim cites file:line;
                           per lane a citation checker and a sizing skeptic
              COMPLETE     critic, two rounds, on different models; price and check each gap
              AUDIT 1      ledger: every shared unit counted once
              REVIEW       different vendor, adversarial, read-only
              VERIFY       per claim: refuter + confirmer, then a decider that sizes only
                           the work the code shows is missing
              AUDIT 2      dedupe verified deltas against late gaps
              ARITHMETIC   a cheap model checks the arithmetic
              PRESENT      state which model did what, and what changed on review
              RETRO        run note + lessons: reinforce, contradict, add
```

## Code ship-gate loop

```
RECALL -> budget rounds by change kind
          (2-3 for a small content or UI gate, 4-7 for privacy or data safety,
           up to about 20 for a new consent flow or state machine)
  -> per round: blind cross-vendor review of ONE frozen commit;
     every finding labelled INTRODUCED or PRE-EXISTING
     (block only on introduced high or medium; send pre-existing items to the owner as decisions)
  -> the driver opens each cited line; a prescribed fix is a hypothesis until reproduced
  -> a failing test per accepted finding, then the fix
  -> the driver states the fix design; prefer removing a mechanism, or matching the baseline,
     over adding a queue, wrapper or cache late in a release
  -> when case-by-case patches keep finding new edges, stop: write one invariant and review
     the code against it
  -> optional: Gemini as a third blind lane on the same commit; it informs, it does not gate
  -> repeat until both blind reviewers approve the same commit; re-review after any change
  -> execution evidence: tests, device or simulator, real data, machine under load
-> RETRO
```

## Rules that keep it honest

- Lanes must not overlap. Two agents on the same files produce contradictory findings and
  double counting.
- Every produced claim carries a citation. A claim with no file:line is a guess.
- The reviewer is read-only. It advises; the driving session owns acceptance.
- Say who did what. If a number changed after review, show the before and after. A report that
  hides its own corrections is less trustworthy, not more.
- Model review does not replace execution. Run the change.
- One simulator-booting or cold-build lane per machine. Check free disk first.
