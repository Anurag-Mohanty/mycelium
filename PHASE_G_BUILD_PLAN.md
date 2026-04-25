# Phase G — Role-Authoring Architecture

**Status:** Build plan. Replaces ongoing prompt-iteration work in Phase F.
**Premise:** Workers fail to hold directives because they're under-equipped for the judgment they're being asked to make. The fix is real role identity authored by managers at hire time, not better worker prompts.
**Scope:** Six builds, sequenced. Two major gates. Six to eight weeks of focused work if integration goes smoothly.

---

## Why this, why now

Two rounds of prompt-level fixes for the leakage problem (detect-decide-justify, then surface-and-commit) both scored 4 of 7 on offline validation. The same three scenarios failed in both attempts. The mechanism wasn't the problem — the worker had no concrete standard close enough to the work to fail against. The charter is too abstract; the scope is too procedural; nothing in between gives the worker something specific to be accountable to.

The architecture this plan builds gives every worker a role identity authored by their manager: a name, a success bar specific to that role, and a heuristic for ambiguous moments. The bar is closer to the work than the charter is, so it fires first. Managers review children against the bars they themselves authored, so accountability is real.

This is the architectural step the strategy doc has been pointing at since the original "no hardcoded roles, but workers actively author hires" position landed. It was deferred through Phase F because Phase F bet that scope language alone could carry enough load. The Phase F runs showed that bet only partially paid off — workers differentiated somewhat by scope, but converged in reasoning, leaked on charter compliance, and ignored the manager-scope designation. All three problems are symptoms of workers being under-equipped. Role authoring addresses them at the source.

The work generalizes. The same machinery that handles npm exploration handles SEC research, marketing campaigns, drug discovery design, or any engagement where a real consulting firm could be hired. We're building exploration first because that's where we have baseline runs and a well-understood corpus. The architecture is engagement-agnostic by design.

---

## What carries forward from Phase F

Phase F infrastructure is not thrown away. The pieces that carry forward:

- Workspaces (org-level and department-level), with charter and rules of engagement deposited at run start
- Genesis producing the charter
- Per-manager synthesis at decomposition points
- Depth freedom governed by budget rather than global cap
- Budget pool with ceiling-not-reservation semantics
- Anomaly target generation and routing

What changes is what the spawn carries, what the worker reasons against, what the Planner produces, and how managers review children. Five components touched, all of them building on Phase F rather than replacing it.

---

## The six builds

Each build has an offline validation step (cheap, fast feedback against synthetic scenarios), an integration test (does it work in the pipeline), and a gate that determines whether to proceed.

### Build 1: Spawn contract extension

**What changes.** Every spawn carries a role definition alongside scope, budget, parent context, and workspace references. The role definition has three fields: role name, success bar specific to this role, heuristic for ambiguity.

**Why first.** Foundation for everything else. Planner can't author roles if spawn doesn't carry them. Workers can't reason against bars if they don't receive bars. Managers can't review against role definitions if no definitions exist.

**Implementation.** Spawn payload schema extends. Worker initialization reads role definition into context. Orchestrator passes role definitions through the same way it passes scope and budget. No reasoning changes yet — workers receive role definitions but don't necessarily use them. Pure plumbing.

**Offline test.** Spawn a single worker with a hand-authored role definition. Verify the worker has the role definition in context. Verify the structure is correct. Verify the worker can reference its role when asked.

**Integration test.** Run a small mini-pipeline where role definitions are hardcoded for npm scopes (Planner extension comes in Build 3). Verify they propagate cleanly. Confirm no breakage in existing Phase F functionality.

**Gate.** Spawn contract carries role definitions reliably. No regressions in Phase F infrastructure.

**Estimated time.** One week.

---

### Build 2: Worker prompt restructure

**What changes.** The NODE_REASONING_PROMPT restructures around role identity instead of scope and charter. The worker reads "you are a [role], your bar is [specific to role], your heuristic when in doubt is [posture]" first. The role's bar fires before the charter check because it's closer to the work. This replaces the surface-and-commit and detect-decide-justify experiments — those scaffolding mechanisms get absorbed into role-anchored reasoning rather than remaining as separate add-ons.

**Why second.** Once spawn carries role definitions, workers need to use them. This build gives the role definition operational power.

**Implementation.** Worker prompt restructures. Role context comes first. Charter still loaded for higher-level standards, but the role's bar is the primary judgment criterion at emission time. Worker reasoning becomes "does this observation pass my role's bar" before "does this observation pass the charter."

**Offline test.** Same seven scenarios from the leakage validation, but each scenario now includes a role definition the worker reasons under. Bar to clear: scenarios 3, 5, and 7 (which failed in both surface-and-commit and DDJ) now pass with properly authored role definitions. If they don't, role definitions aren't as load-bearing as we expect, and we revisit before continuing.

**Integration test.** $2 mini-run on npm with hand-authored role definitions for the top-level scopes. Compare worker behavior to previous mini-runs. Look for: workers reasoning against role bars, shape leakage closing, artifact handling improving, convergence reducing.

**Gate.** Offline scenarios show role definitions resolve the failing cases. Mini-run shows behavioral change consistent with role-anchored reasoning. No regressions.

**Estimated time.** One to two weeks.

**Note.** This is the **mid-build gate**. See [Major gates](#major-gates) below.

---

### Build 3: Planner extension to organizational design

**What changes.** Today's Planner produces scopes with budgets. The new Planner reads the charter and corpus shape and produces an organizational design — what departments are needed, what each department head's role is, how the work flows (parallel, sequential, staged-with-feedback), how the budget allocates across departments. Each department head spawns with a role definition the Planner authored.

**Why third.** Once workers can use role definitions, the Planner becomes the system that authors them at the top of the tree. The Planner is the executive layer where the hardest cognitive work happens. Quality here cascades through the whole run.

**Implementation.** Planner prompt extends substantially. It reasons about engagement type (currently always exploration; the prompt should be agnostic to support future modes). It designs the organization — what departments, what role each department head plays, how work flows between them. It authors role definitions for each department head.

**Offline test.** Run the new Planner against multiple synthetic charters: an npm charter, an SEC charter, possibly a marketing-style charter for cross-task validation. Read the organizational designs it produces. Are the role definitions concrete enough to be useful? Are the success bars specific to each role? Are the workflow shapes appropriate for the engagement type?

The hard test: give the Planner the same npm charter twice and verify it produces similar (not identical) organizational designs. Wildly different designs each time means Planner reasoning is too unstable to trust with executive decisions. Designs that vary in detail but converge in shape is healthy.

**Integration test.** $2 mini-run on npm with the new Planner authoring real role definitions (no more hardcoded definitions from Build 1's plumbing test). Compare to the Build 2 mini-run. Does Planner-authored role quality match hand-authored quality? Where does it differ?

**Gate.** Planner produces sensible organizational designs across multiple charter types. Mini-run with Planner-authored roles produces behavior comparable to mini-run with hand-authored roles.

**Estimated time.** Two to three weeks.

---

### Build 4: Manager Turn 2 reorientation

**What changes.** Today managers receive children's outputs and decide whether to fund continuation. The new manager Turn 2 reviews each child's work against the role definition the manager itself authored. Accountability becomes real because the standard was real at hire time. Managers can identify when their hiring was wrong and adjust.

**Why fourth.** Once managers are authoring role definitions for their hires (which Build 3 enables at the top level and recursive spawning enables below), they need to review against those definitions. This build closes the accountability loop.

**Implementation.** Manager Turn 2 prompt extends. The manager reads each child's output alongside the role definition the manager authored for that child. The review asks: did this child meet the bar I set? If not, was it because the child reasoned poorly, or because I authored the wrong role? The manager either rebriefs, hires differently, or accepts the gap.

**Offline test.** Construct synthetic scenarios where children produce outputs of varying quality against authored role definitions. Test whether the manager correctly identifies which children met their bar and which didn't. Test whether the manager correctly distinguishes "child reasoned poorly" from "I authored the wrong role." This is harder than it sounds — it requires the manager to have meaningful self-awareness about its own authoring quality.

**Integration test.** $2 mini-run with Builds 1-3 in place. Look at manager Turn 2 outputs. Are managers actually reviewing against role definitions, or are they summarizing as before? If reviewing, are the reviews useful?

**Gate.** Manager Turn 2 reviews show evidence of evaluating children against authored role definitions, not just summarizing.

**Estimated time.** One week.

---

### Build 5: Formation-time assessment

**What changes.** Every node, on formation, runs the assessment: "with my budget and my role, can I do this work alone, or do I need to hire?" If alone, do the work. If hire, become a manager and author roles for hires. The recursion is identical at every layer.

**Why fifth.** This is the mechanism that makes decomposition reliable rather than aspirational. Today the Planner can mark scopes as manager-level and workers can ignore that designation and investigate directly anyway (we saw this in run 16dc20f6, where three of four manager-level top-level workers resolved without decomposing). With formation-time assessment, every node makes the call explicitly. The role definition the node carries makes the assessment concrete — a worker assessing "can I do this alone" against a specific role and bar reasons differently than a worker assessing it against a generic scope.

**Implementation.** Worker prompt extends with an explicit formation-time step that runs before the worker does any other work. The step asks: given my role and budget, can I cover this scope to my role's bar? If yes, proceed to investigation. If no, identify what hires are needed, author their roles, allocate budget across them, spawn them, become a manager.

**Offline test.** Construct synthetic scenarios where the right answer is "investigate alone" and scenarios where the right answer is "hire." Vary the budget — same role, different budget, different right answer. Verify the assessment makes the right call.

**Integration test.** $2 mini-run. Look at decomposition behavior. Are workers receiving manager-level scopes actually decomposing? Are workers receiving worker-level scopes investigating directly? Does the tree shape match the work?

**Gate.** Decomposition behavior matches scope characteristics. No more workers ignoring manager-level designations.

**Estimated time.** One week.

---

### Build 6: Reader test instrumentation

**What changes.** The reader test from the Phase F design finally gets built. After the run produces findings, a reader-test pass scores each finding against the charter's standards. Output is a per-finding score with reasoning, written to metrics.json.

**Why sixth.** The reader test was always the gate Phase F was designed around. It was deferred because there was no point scoring findings while leakage contaminated the output. Now that the architecture should produce real findings, the reader test becomes meaningful. This is the gate that determines whether the architecture as a whole is working.

**Implementation.** After report generation, a reader-test scorer runs over each finding. The scorer is an LLM call with the charter in context, asking: would a knowledgeable reader of this corpus say "I didn't know that" about this finding, or "sure, that's known"? Per-finding score (yes / marginal / no) with reasoning.

**Offline test.** Score historical findings from previous Mycelium runs and from the Federal Register baseline (the State-Commerce arms export finding, which we know was good). The scorer should rate the Federal Register finding "yes" and rate previous Mycelium leakage findings "no." If it doesn't, the scorer needs calibration work before it can be trusted as a gate.

**Integration test.** Run on the $10 npm output from the new architecture. Then run on the $10 SEC output. Both should produce some "yes" findings if the architecture is working.

**Gate.** Reader test correctly identifies known-good and known-mediocre findings on historical data. On new architecture runs, at least three findings score "yes" on at least one corpus.

**Estimated time.** One week.

---

## Major gates

Beyond the per-build gates, two larger checkpoints determine whether the whole effort is working.

### Mid-build gate: After Build 2

**The question.** Do role definitions, when present, change worker behavior in the way we expect? This is the load-bearing assumption of the entire architecture.

**Specifically.** Offline scenarios should show that scenarios 3, 5, and 7 (which failed in both leakage-fix attempts) now pass with properly authored role definitions. The mini-run should show observable behavior change in the wild — specifically, no shape leakage, better artifact handling, less convergence.

**If this gate fails.** Stop and reconsider. We've validated the architecture on its central claim before investing in Builds 3-5. Possible failures: role definitions don't change worker behavior even with hand-authoring (architecture premise wrong), or hand-authored roles work but the seven scenarios were too easy (validation needs harder scenarios before we trust the result).

**If this gate passes.** Proceed with confidence that the foundation is real.

### Final gate: After Build 6

**The question.** Does the architecture, fully built, produce findings that pass the reader test on real corpora?

**Specifically.** At least three reader-test-passing findings on npm at $10. At least three on SEC at $10. Both demonstrate the architecture isn't just well-tuned to one corpus.

**If this gate fails.** We have a working architecture that doesn't produce great work yet. The next investments are in role-authoring quality (better Planner reasoning, possibly AutoResearch-style prompt evolution on role definitions) rather than further architectural changes. The architecture is the substrate; quality on top of it is the next layer of work.

**If this gate passes.** Phase F's gate is met. We have an autonomous reasoning organization that produces genuinely novel findings on data corpora. Time to ship — visualizer, essay, repo, launch — and then the deferred capabilities (cross-task generalization, persistence, mixed-model routing) become the next horizon.

---

## What's deferred

Worth being explicit about what's not in this plan so it doesn't feel like we're skipping things:

- **Cross-task generalization.** The architecture is built engagement-agnostic but only validated on exploration. Marketing campaigns, drug discovery design, etc. come after the core lands and the launch happens.
- **Constraint propagation workspaces.** Real need for engineering engagements (Mars missions, electronics) but not on the critical path for exploration. Deferred to post-launch.
- **Adversarial role authoring.** Useful for litigation and political engagements; not needed for exploration.
- **Dynamic re-planning.** Useful for engagements where conditions change mid-engagement. Not needed for typical exploration runs.
- **Persistence across runs.** The v3 work in the strategy doc. Important for long-horizon engagements like drug discovery. Not needed for single-shot exploration.
- **Mixed-model routing for compute efficiency.** Real opportunity but not on the critical path. Worth pursuing after the architecture is producing findings worth optimizing for.
- **AutoResearch-style role evolution.** Once role definitions exist as artifacts and the reader test gives us a fitness function, we can evolve role definitions over iterations. Probably the first post-launch investment because it compounds.

---

## Critical path and timeline

Smooth path: six to eight weeks from start to final gate. Earliest meaningful evidence comes after Build 2 (two to three weeks in) at the mid-build gate.

If integration surfaces problems: ten to twelve weeks. Most likely problem areas are Build 3 (Planner authoring role definitions well requires real reasoning and may need iteration) and Build 6 (reader test calibration may need work).

Sequencing dependencies are strict: each build depends on the one before it. Builds 1-3 are the heaviest. Builds 4-6 are lighter and faster once the foundation is in place.

---

## Risks

**Build is bigger than recent work.** Six builds with real validation between each is a substantial investment. Not a Phase F patch — the architectural step the strategy doc has been pointing toward for months. Worth being clear that this is a meaningful commitment of time.

**Architecture rests heavily on Planner quality.** If the Planner authors weak role definitions, the cascade beneath produces weak work, and we won't always be able to tell whether failure was authoring or execution. The mid-build gate (Build 2 with hand-authored roles) helps isolate this. If hand-authored roles work but Planner-authored roles don't, we know to focus on Planner improvement (likely via role-evolution after the launch).

**Manager Turn 2 self-awareness is genuinely hard.** Asking a manager to distinguish "my hire reasoned poorly" from "I authored the wrong role" requires real self-evaluation. This may need iteration in Build 4. Not a blocker, but worth flagging.

---

## What success looks like

When all six builds land and both major gates pass:

- Workers spawn with real role identity, not generic worker prompts
- Workers reason against role-specific bars at every emission decision
- Managers author role definitions when they hire and review against those definitions
- Decomposition happens reliably because every node assesses at formation
- Synthesis combines distinct angles from differently-authored children, not summaries of similar findings
- Findings on npm and SEC pass the reader test
- The architecture is ready to extend to other engagement types as the next horizon

The deeper outcome: Mycelium stops being a discovery framework and becomes a substrate for autonomous reasoning organizations. Discovery is the first engagement type. Marketing campaigns, M&A diligence, drug discovery design, and other engagements that real consulting firms handle become the natural extension path. The architecture is engagement-agnostic by design; we're building it that way from the start so the future extension is integration work, not redesign work.
