# Confirmed orchestration rules

These rules come from the maintainer's ledger of real runs. The ledger held 247 lessons. 62 lessons were confirmed by two or more distinct runs, and they appear below as 61 rules (two lessons were merged).
They are one maintainer's priors, not a benchmark and not laws. Each rule shows how many runs support it.
Re-measure them on your own work with `fo.py lessons`.

## routing

**Run a blind tournament for a design fork.**
Why: Blind entries from two vendors gave a clear winner. Cross-rating caught a platform guideline violation. Grafting kept good ideas from the losers.
Apply: Design fork -> get blind entries from Claude and Codex. Have each side rate the other, or use a judge panel. Pick one winner. Graft the best ideas of the other entries into it.
(x5 runs, ledger id FO-L040)

**Split lanes by model strength. Treat a small routing model as advice only.**
Why: This split held across many runs. Haiku citation checks held 126 of 129 times. A small routing model scored close to the frontier models, so it can advise but not authorize.
Apply: Broad sweep -> Sonnet. Skeptic or critic -> Opus. Citation check -> Haiku. Root-cause or adversarial review -> Codex. Never let a routing score authorize an action.
(x3 runs, ledger id FO-L028)

**Check which model ids are reachable before a cross-vendor run.**
Why: A router can reject ids that are not in its policy. It can cache the policy per process. Different sessions mapped the same alias to different models on the same day.
Apply: Before a cross-vendor run -> list the newest model and the live routing policy. Pass the exact model id when the newest model matters. After a policy edit, reconnect the tool or use a read-only CLI call in the meantime.
(x3 runs, ledger id FO-L012)

**Use the top model only to orchestrate and synthesize.**
Why: Runs that omitted the model inherited the expensive one. Runs without effort caps spent more. Synthesis in costly lanes added cost. A usage limit on the top model also stopped parallel lanes.
Apply: Launching lanes -> set an explicit model and effort on every lane. Do dedupe and merge in code, not in an agent.
(x2 runs, ledger id FO-L027)

**Run ideation as a blind council across two vendors.**
Why: Each member brought unique ideas the others missed. The shared core gave a safe base to build on. A slow member did not block the build.
Apply: Ideation task -> run three members across two vendors on one frozen packet. Build from the ideas that two members share. Fold each member's unique ideas in before the first review round.
(x2 runs, ledger id FO-L124)

## reviewer-prior

**Run two vendors blind on the same packet at every ship gate.**
Why: In four runs each vendor missed the other's best finding. Astra finds concrete control-flow defects. Sol finds missing categories and framing about resources, abuse and durability.
Apply: Ship gate, plan review or council -> run gpt-6-astra and the current Sol blind on one packet. Expect each to find a class of problem the other misses.
(x30 runs, ledger id FO-L004)

**Record reviewer hit rates by artifact kind. Set verification depth by kind.**
Why: The same models were near-perfect on code HIGH findings. They were much weaker on content facts and size estimates. One fixed prior misleads in every kind but one.
Apply: After each run -> record hit rate per model and per artifact kind. Code HIGH findings mostly hold. Content fact flags hold about 60 to 75 percent. Estimate-size asks keep only about 1/12 to 1/3. Verify more where the rate is lower.
(x17 runs, ledger id FO-L005)

**Treat a finding that two blind reviewers share as real.**
Why: Shared findings held every time they were opened. Disagreements were settled by the code, not by a third opinion.
Apply: Both reviewers name the same top blocker -> fix it first. They name different top items -> open both files. Do not average. Do not trust the more confident reviewer.
(x5 runs, ledger id FO-L009)

**Give Sol an explicit scope boundary in every round.**
Why: Sol widens its scope each round. One loop did not converge until the packet named a boundary.
Apply: Writing a round packet for Sol -> state the scope limit, for example "only the fields the cards read".
(x2 runs, ledger id FO-L008)

**Put every price delta into the review packet.**
Why: Most of one reviewer's raise on a verified ledger was work a later delta already priced. The reviewer asked again because it did not see the delta.
Apply: Reviewing an estimate ledger -> include all delta files that re-price it. Check each reviewer size ask against those deltas before you accept a number.
(x2 runs, ledger id FO-L036)

## review-loop

**Ship only when both blind reviewers approve the same final commit.**
Why: Verdicts flipped between rounds. One reviewer approved a build the other broke. Later rounds kept finding real defects, including one after an approval, in an unsafe follow-up change.
Apply: After every fix round, and after any change made after an approval -> run a new round. Stop when a round finds only documentation-level items.
(x30 runs, ledger id FO-L006)

**Make reviewers label each finding INTRODUCED or PRE-EXISTING.**
Why: Without labels one loop stayed at REJECT for six rounds. Each fix to shared code surfaced old debt. With labels, loops converged. Old gaps went to the owner.
Apply: Round 1 -> tell each reviewer to label every finding. Block the gate only on introduced high or medium findings. Send each pre-existing item to the owner as a separate decision, or as a pilot-first rollout with a tracked fix.
(x21 runs, ledger id FO-L001)

**Replace case-by-case patches with one principle.**
Why: A consent review converged only after a principle replaced patches. Game reviews looped until the coordinator wrote a rulings file. A counsel named a pre-code ownership gate as the way to stop churn.
Apply: Review keeps finding new edge cases after patches -> stop patching. Write one principled model or rulings file. Review the code against that model.
(x17 runs, ledger id FO-L038)

**Probe a defect class in every sibling artifact.**
Why: In one run a hidden-screen focus defect surfaced in one game per round. A five-minute probe across all siblings then found it in three more at once.
Apply: A review finds a defect in one artifact of a set -> test that defect class mechanically in every sibling before the next round.
(x4 runs, ledger id FO-L091)

**Build each review packet from live data. State what is proven and what is not.**
Why: Verdicts moved only when gaps became disclosed data. A guessed timezone and a hardcoded field were judged store defects. Naming what is proven kept the review on what is not.
Apply: Writing a blind review packet -> use the live store. Verify every fact in the brief on the machine. List proven items and each coverage gap as data (null is not zero). Tell the reviewer to answer "not found" instead of guessing.
(x3 runs, ledger id FO-L025)

**Re-read your own evidence before you send work to review.**
Why: In one plan, 2 of 8 review findings contradicted a lane report the producer had already read. A producer re-read catches these overclaims.
Apply: Before review -> re-read the evidence your artifact cites. Fix every claim the evidence already contradicts.
(x3 runs, ledger id FO-L076)

**Run two critic rounds on different models, then a ledger audit, after any per-item pricing.**
Why: A second critic round found the most important gap that six lanes and round 1 had missed. Ledger audits removed days of double counting that per-item judges cannot see.
Apply: Per-item estimate step finished -> run critic round 1 and critic round 2 on different models. Then audit the whole ledger.
(x2 runs, ledger id FO-L052)

**Review the written spec of an auth or security feature before you build.**
Why: Two vendors found 12 spec-level defects before any code existed. Later code rounds found only small items.
Apply: Auth or security feature -> run a blind two-vendor review of the written spec before implementation lanes start. Fold the rulings into the spec as binding sections.
(x2 runs, ledger id FO-L151)

**Review a product-facing summary as a new artifact.**
Why: Condensing many approved rounds into slides added nine wrong or overstated sentences that no earlier round could see. A cosmetic fix after the freeze cost one more round.
Apply: You condense reviewed engineering evidence for a product audience -> give the result its own blind cross-vendor round. Freeze it only after the last layout change.
(x2 runs, ledger id FO-L179)

**Describe what a static render shows. Mark behavior as intent.**
Why: In one review, 6 of 11 findings were behavior verbs applied to static renders. Every one held.
Apply: Writing about an artifact built from static renders -> write what the picture shows (renders, sits, is drawn). Label behavior (fits, updates, returns, keeps) as intent until a device run confirms it.
(x2 runs, ledger id FO-L189)

## fix-design

**In risky code, fix by removal, not by a new mechanism.**
Why: In each measured loop, rounds that added a mechanism produced a new HIGH finding or data loss. Rounds that removed one passed. A fix that kept its state in the same atomic write passed first time.
Apply: A review round fails ordering, lifecycle or state-machine code -> remove a mechanism, use compiler truth, or match the baseline behavior. Do not add a new lexer, cache, publication or generation mechanism late in a release.
(x15 runs, ledger id FO-L002)

**Treat a reviewer's prescribed fix as a hypothesis.**
Why: In two runs the finding was real but the prescribed fix was wrong. One fix for SDK ordering still leaked in a trace. One style fix could not work in a fixed-position stacking context.
Apply: A reviewer prescribes a fix -> prove it with a designed trace or probe before you ship it.
(x12 runs, ledger id FO-L003)

**Reproduce each accepted finding as a failing test. Log every waiver.**
Why: A failing-first test proves the finding was real and that the fix closes it. A ledger of fixes and waivers let the coordinator end loops on evidence, not on reviewer fatigue.
Apply: Accepted finding -> write a failing test first, then fix. Record each other finding in the review ledger as a ruled waiver with its reason.
(x8 runs, ledger id FO-L039)

**Put the design decision in the worker brief.**
Why: Rounds that sent "fix finding N" bred new mechanisms. Rounds that sent a decision (what to delete, which invariant must hold) closed whole classes of findings. A worker report that a proposed API crashes a driver stopped a bad decision before review.
Apply: A finding has more than one fix -> state the decision in the brief. Require the worker to report any decision it could not follow, with its reproduction.
(x4 runs, ledger id FO-L048)

## verification

**Do not let model review replace execution.**
Why: Across many runs, device runs, tests, real-data runs and a loaded machine found real defects that unit tests and model reviews missed. Lifecycle and animation bugs (cold-launch on a hidden pane, a task keyed on stale geometry) appeared only at runtime. Static review found 9 issues, but 3 shipping-grade bugs appeared only in UI runs.
Apply: After a change -> run it with tests, on a device or simulator, against real data, and under machine load. Run the real launch and layout matrix on the simulator between review rounds. (Merged: FO-L022, FO-L147.)
(x23 runs, ledger ids FO-L022, FO-L147)

**Review and verify in an isolated checkout at a frozen commit.**
Why: A shared or moving checkout changed under concurrent tasks and voided their evidence. One fact-check inspected a different commit than the one stated.
Apply: Every evidence block -> name the commit it tested. Check that the reviewer read that commit.
(x8 runs, ledger id FO-L017)

**Break the code on purpose before you accept a green test.**
Why: Several green results measured nothing. Zero tests ran. An assertion fired for only one word order. A counter scored host-written output. A test passed only through a fallback branch.
Apply: An agent reports a green test -> break the production code and confirm the test goes red. Confirm the run executed a non-zero test count. Confirm the assertion fires on natural input.
(x7 runs, ledger id FO-L020)

**Suspect the harness first when a runtime check fails.**
Why: In one matrix, 9 failure causes were harness defects and 2 were app defects. A parser miss looked exactly like the missing event under test.
Apply: A step of a runtime check fails -> diagnose the harness first (driver, parser, mock, flag). Make a new harness prove itself with a parser identity audit, lifecycle markers, a host proof and a base red control.
(x7 runs, ledger id FO-L062)

**Run an independent claim judge before you ship an evidence ledger.**
Why: A judge recounted every number from the run files and flagged unsupported claims. It also found a pre-existing defect that no checker rule encoded. A cross-vendor auditor found the same defect independently.
Apply: Evidence ledger ready -> run a claim judge that recounts every number and checks per-run provenance. Give it the raw-data tables.
(x5 runs, ledger id FO-L063)

**Treat an unrelated gate failure as a possible flake, and prove it.**
Why: A quiet re-run hides a real race. Blocking on a load flake wastes the gate. A full-suite red plus isolated green runs separates the two.
Apply: A final gate fails in code the change did not touch -> keep the red run on record. Re-run the suite once at the same commit. Run the failing test alone many times. Ship only on that evidence. Never re-run until green without saying so.
(x5 runs, ledger id FO-L118)

**Open the source before you route or rule on a fact.**
Why: A stale line in a memory index nearly reopened a closed decision. A generated review page stated a wrong status that the call transcript refuted.
Apply: Before routing or ruling on a fact -> open its source body or the live system. Do not trust a summary, an index line or a generated page.
(x4 runs, ledger id FO-L043)

**Read each workflow lane's result yourself.**
Why: Lanes hit output deadlines and returned "NOT YET VERIFIED". A mock lane reached production auth and created accounts. Report subagents refused to write files.
Apply: A lane finishes -> read its integrate result. Run any suite it did not verify. Prove that its mock mode cannot reach production. Write report files in the coordinator from the lanes' returned fields.
(x3 runs, ledger id FO-L019)

**Measure visual checks. Do not pass a picture on a glance.**
Why: Glance checks passed crops, missing offsets and clipped views that the owner then found. Pictures at several text sizes caught layout regressions after merges.
Apply: Any visual check -> measure ratios, offsets and positions from several views or text sizes. Write the numbers in the report.
(x3 runs, ledger id FO-L023)

**Give each finding from a Claude review panel its own verifier.**
Why: Raw panel findings were refuted at rates from 4 to 40 percent. Without a verifier, fix rounds would chase false findings.
Apply: A panel returns findings -> assign each one an independent verifier or skeptic before it enters a fix round. Expect 4 to 40 percent to fall.
(x3 runs, ledger id FO-L037)

**Freeze the harness for a counted run.**
Why: A parser was replaced during one run of a matrix, and a watcher was edited after it. Two auditors flagged that the exact tools behind the results were not kept. The matrix had to run again.
Apply: Before the first counted run -> copy every tool and mock into the run folder with its sha256. Never change a tool while a matrix runs.
(x3 runs, ledger id FO-L065)

**Record the test data your runs used.**
Why: Real test data changes during a session. One listing changed in one feed but not in the other, so two hosts would have run different items.
Apply: Counted runs -> probe the data right before and right after. Probe again before any later re-run of the same scenario.
(x3 runs, ledger id FO-L068)

**Re-run the in-app proof on the final artifact set.**
Why: A series note can list files that a run never loaded. A full UI pass with default inputs proves no regression. It does not prove the new behavior.
Apply: Before the final report -> run the in-app proof with the exact final artifacts at the shipped head. Check how each earlier run got its inputs (an environment root, a default path).
(x3 runs, ledger id FO-L120)

**Drive a replacement library inside the real host app.**
Why: Both blind reviewers approved and the demo host matched the shipped wire. The real app still fired an analytics event 20 seconds early with the wrong page context. Only the host's page state showed it.
Apply: Before you call a replacement library shippable -> run it once in the real host app.
(x3 runs, ledger id FO-L136)

**Build ledger versions with a script. Pin every seed.**
Why: Unpinned hash seeds changed a deterministic launch date. After pinning, an auditor's rerun was byte-identical and every total recomputed with zero mismatch.
Apply: Each ledger version -> use a script that asserts every old value and writes one change row per edit. Pin every random and hash seed in the schedule model. Have an independent auditor rerun it and compare the output byte for byte.
(x2 runs, ledger id FO-L045)

**Check how a screenshot was captured before you call it "today".**
Why: Pictures labeled "live build" and "today" were captured with a non-default launch variant. No capture of the shipping variant existed.
Apply: Before you label a screenshot as current -> check which launch variant or flag it used.
(x2 runs, ledger id FO-L057)

**Count production answers by the proxy's applied-rule record.**
Why: A URL filter counted answers from an old mock rule as production. A flow cap also cut the window.
Apply: You claim that production answered -> count requests by the proxy's per-flow applied-rule record, per app process and under the query cap. Do not use a URL filter.
(x2 runs, ledger id FO-L066)

**Check each build target's include and exclude lists for every added file.**
Why: A new platform-specific file compiled locally. It would have broken another target whose spec excludes each such file by name. Only a reviewer caught it.
Apply: A release change adds a file -> check each target spec's explicit include and exclude lists. A single-target local build cannot see this break.
(x2 runs, ledger id FO-L070)

**Read the corrected verdict when skeptics reject a finding.**
Why: In one run, 33 of 110 refuted rows still held a real mismatch under a different label. Filing them as non-findings stranded two HIGH contracts.
Apply: A skeptic rejects a finding -> read the corrected verdict before you file it as refuted.
(x2 runs, ledger id FO-L099)

**Rerun override tests after content gets new served names.**
Why: A debug override matched the exact served file name. After a publish renamed the files, the app silently loaded the live content. Tests failed with no error that named the cause.
Apply: A publish gives files new served names -> rerun every test that overrides a served file by name, against a freshly synced mirror. Keep the keys valid with an old-to-new rename map, or key by content identity.
(x2 runs, ledger id FO-L121)

**Render hardware states with a harness when the simulator cannot set them.**
Why: One simulator setup could not change a fold posture. A harness produced 10 posture states in two build cycles.
Apply: The simulator cannot change a hardware state (fold posture, display) -> render each state with a test harness at the sizes your own test fixtures use. Do not wait for a GUI.
(x2 runs, ledger id FO-L129)

**Check the app's own calls before you blame a vendor SDK.**
Why: A claim that an SDK dropped an event was refuted by both blind reviewers. The app mounted the panel before the start call returned and never called several host APIs.
Apply: A vendor SDK event is missing -> list the SDK host APIs the app never calls. Compare the app start order on the failing path. Do this before you route the issue to the vendor.
(x2 runs, ledger id FO-L205)

## estimating

**Budget review rounds by change kind before the loop starts.**
Why: Measured loops ran from 2 to 19 rounds. The count followed the kind of change.
Apply: Planning a review loop -> budget 2 to 3 rounds for small content or UI gates. Budget 4 to 7 for privacy or data-safety changes. Budget up to about 20 for a new API consent or state machine. Allow about 4 minutes per reviewer per round.
(x8 runs, ledger id FO-L007)

## scheduling

**Run one heavy lane at a time on one machine.**
Why: Parallel heavy lanes hit the per-user process limit or a full disk. Then every shell call fails, and the coordinator cannot recover.
Apply: Simulator-booting or cold-build lanes -> run at most one at a time. Check free disk first (about 4 GB of build cache and 2 GB per simulator per mobile lane, and 15 to 20 GB per cold large-app build). Make each lane shut down its simulators and delete its worktree and build cache when it ends.
(x7 runs, ledger id FO-L016)

**Make sure something wakes you when a background review ends.**
Why: Background jobs do not wake the session. Without a waiter the coordinator does not learn that a verdict arrived.
Apply: You start long reviews in the background (for example `fo.py ask` in a background shell) -> use the host's completion notice, or start one background waiter that watches the out files. Never assume a verdict arrived.
(x2 runs, ledger id FO-L015)

**Send the schedule model code to the reviewer, and list what to check.**
Why: Reviewer size asks mostly failed verification. Schedule-model findings held almost every time, and some were worse than stated.
Apply: Schedule review -> send the model code and its rates. Check for gates tied to no built work, approval clocks that start before their filings, reversed dependency arrows, same-file parallel work, savings from unbuilt options, and double discounts on capacity.
(x2 runs, ledger id FO-L035)

## harness

**Test Codex with a cheap liveness ask before a workflow depends on it.**
Why: Wrapper agents fall through silently when Codex fails. A cross-vendor review is lost, or the counsel looks like it failed on its merits.
Apply: Before a workflow uses Codex -> run a cheap liveness ask and read any failure. A status-list timeout is transient, so retry as a fresh job with optional context off. A 401 from a service-account key is not transient, so the owner must log in again.
(x8 runs, ledger id FO-L010)

**Harden `codex exec` when you use it as a fallback.**
Why: A background `codex exec` waits for stdin forever. Two 25-minute review runs were lost to this hang.
Apply: Using `codex exec` as a fallback -> make stdin reach end of file (`</dev/null`, or pipe the prompt with `-`). Write the answer to a file with `-o`. Use `-s read-only`. Put a time limit on the process.
(x4 runs, ledger id FO-L011)

**Prepare for a release review.**
Why: On low disk a capture hook dropped the final answer, so a reviewer twice looked like APPROVE with no review. A default ask stops at 20 tool calls, which is too thin for a release review.
Apply: Before a release review -> free at least 10 GiB of disk. Request an extended budget. Treat an approval with no review body as a lost answer. Recover it from the job's message events.
(x3 runs, ledger id FO-L013)

**Give a visual reviewer a fixed list of pre-rendered screenshots.**
Why: An unbounded self-rendering reviewer looped for two hours on stale pages. The bounded replacement took about 70 seconds and found 28 problems.
Apply: Visual or layout review -> supply pre-rendered screenshots, each opened once per mode. Never let the reviewer render its own in an open loop.
(x2 runs, ledger id FO-L024)

**Put the directory change in each background or chained command.**
Why: Background chains inherit the launch directory. Patches and builds ran in the wrong checkout. One lane reintroduced code the coordinator had removed.
Apply: Any background or chained step -> put `cd` in the same command. Check test counts to prove which checkout ran. Tell each lane about coordinator edits so it does not revert them.
(x2 runs, ledger id FO-L047)

**Drive UI by label and require proof of arrival.**
Why: Remote config changes the UI a driver depends on. Production config added a tab and a profile screen at every launch, so a fixed tab tap opened the wrong tab. The run failed for a harness reason.
Apply: A run against production config -> find every UI target by its text or accessibility label. Require positive arrival evidence (a screen marker or an analytics page view). Stop the run when a navigation step fails. Never tap fixed coordinates.
(x2 runs, ledger id FO-L064)

**Expect preview listings to rotate. Use a candidate list.**
Why: A listing that carried a whole morning comparison was gone by midday. Two A/B rounds stopped at the guide step until a candidate list fixed the driver.
Apply: A real-app tour uses a preview listing -> give the driver a candidate list. Scroll to the top of the guide before each candidate. Accept the first one that shows the feature.
(x2 runs, ledger id FO-L154)

**Write UI-test screenshots straight to the host. Do not build during a runner start.**
Why: Three tours lost all 24 frames to an unfinalized result bundle. A UI-test runner started under a concurrent build died before it connected.
Apply: A UI-test driver -> write screenshots straight to a host directory from the test process. Kill the build tool about 90 seconds after the test run reports done. Never run an app build while a UI-test runner starts.
(x2 runs, ledger id FO-L155)

**Map table rows into the ask's evidence fields.**
Why: Two long high-effort lanes returned only a verdict. The requested rows were lost and had to be re-run. The ask result keeps only its fixed fields.
Apply: An ask must return a table or catalogue -> tell the lane to put every row into the contract's evidence entries (path, detail). Custom JSON the packet asks for is dropped.
(x2 runs, ledger id FO-L208)

## release

**Get three things before any external build or upload.**
Why: The owner does not trust a build that one model wrote and judged. An upload made without the owner's word had to be corrected. One go never carries forward to the next build.
Apply: Before an external build or upload -> get simulator evidence on production data, a cross-vendor APPROVE after fixes, and the owner's explicit go for that exact build.
(x3 runs, ledger id FO-L031)

**Send a model disagreement on a one-way decision to the owner.**
Why: One-way costs (persistent injected instructions, live-store changes, deletion) belong to the human. Both runs separated defaults the agent may take from rulings that must wait.
Apply: The Claude chair and the cross-vendor counsel disagree on a one-way or destructive decision -> let neither model decide. Set a safe default. Send the question to the owner as a ruling.
(x3 runs, ledger id FO-L032)
