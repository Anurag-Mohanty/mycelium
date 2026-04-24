# Phase F Design — Emergent Organization with Workspace Fidelity

**Status:** Third draft. Replaces the first and second drafts. The first was wrong because it hardcoded a role population. The second was closer but still smuggled in two bad patterns: specifying what the Planner should produce (hardcoding output structure) and treating documents as if they were free. This draft corrects both.

---

## The premise

Mycelium runs a task through three roles — a CEO who gives the directive, a program office that turns the directive into operational reality, and an organization of workers who execute and recursively decompose. The CEO sets purpose and standards. The program office derives operational structure from the directive. Workers investigate their scopes, and when the work is bigger than one worker can hold, they become managers and do the program office's job for their subtree. The directive, the operational rules, and the scopes live in shared workspaces — org-level for the whole organization, department-level scoped to a subtree. Workers reference these workspaces when they need authoritative guidance rather than carrying paraphrased versions in their prompts. Fidelity is preserved because the CEO's voice stays the CEO's voice at depth 5, since depth 5 reads the same workspace that depth 1 reads. Depth itself is not capped globally — it's governed by budget and manager judgment, with rules of engagement articulating the policy.

---

## The architecture in metaphor

**Genesis is the CEO.** Reads the corpus. Knows what's already known. Knows what the organization is being asked to find. Addresses the whole organization in a town-hall voice. Sets the stakes. Tells the organization what impresses leadership and what doesn't. Produces the organizational charter and deposits it in the org-level workspace. Does not design the organization. Does not set operational rules. Does not define areas of work.

**Planner is the program office.** Takes the CEO's directive and translates it into operational reality for this particular task. Decides how the mountain gets divided for this task. Sets the budget shape. Writes the rules of engagement that follow from the directive. The specific operational structure is the Planner's derivation — it's not specified by the design. A different charter would produce different operational structures.

**Workers are the organization.** Each worker receives a scope from the program office (or from its parent manager). References the charter for judgment of quality. Operates within the rules of engagement. Either does its work directly or decides to decompose. If it decomposes, it becomes a manager for its subtree and does the program-office job one layer down. When its subtree completes, it synthesizes what its team found into a department report.

**The recursion is identical at every layer.** A manager at depth 4 does the same operation the program office did — takes its scope, divides it, allocates budget, writes sub-scopes, inherits rules, then synthesizes when children return. Every layer is structurally the same job. The organization emerges from this recursion rather than being imposed.

---

## Workspaces

The architecture uses shared workspaces, not document-paths. This matters because it changes how context flows.

**Org-level workspace.** Created at run start. Contains the charter (from Genesis) and the rules of engagement (from Planner). Every worker in the organization has access to this workspace. It is authored once, read many times, never paraphrased.

**Department-level workspaces.** Created when a manager decomposes. Contains the manager's scope specialization (how the manager articulated the children's sub-scopes), any rule addenda the manager added for its subtree, and the manager's own notes if relevant. Scoped to that manager's subtree. A worker at depth 5 has access to the org-level workspace plus every department-level workspace from root to its own parent.

**Why workspaces over documents-as-paths:** a workspace is a conceptual unit that maps cleanly to how the LLM actually handles shared context. When the infrastructure supports prompt caching, identical workspace content shared across worker calls can be cached and charged once — the savings compound with tree size. When the infrastructure doesn't support caching, workspaces still give us fidelity (one source of truth per scope, not paraphrase chains) and structural compression (workers read when needed, not by default). The concept holds regardless of what the underlying platform supports.

**What goes in which workspace.** The charter goes in the org-level workspace because it applies to every worker. The rules of engagement go in the org-level workspace because operational policy is organization-wide. A manager's sub-scope articulation goes in the department-level workspace because it's specific to that subtree. A manager's rule addenda (where permitted) go in the department-level workspace. Individual worker turn-state never goes in any workspace — it's private to the worker.

**Read cost:** workspaces are tokens when read. A worker that needs to check the charter's standards pays tokens to read that section. A worker that doesn't need the charter for a given turn doesn't read it and pays nothing. The design depends on workers being disciplined about when to read — that discipline comes from the rules of engagement and from how worker prompts are structured. Most turns won't need the charter; the turns that do need it (decisions about whether an observation meets the bar) are exactly the turns where the cost is justified.

---

## Genesis

Genesis reads the corpus, the briefing content, and the survey results. Reasons about what the organization is being sent into and what it's being asked to find. Writes the charter and deposits it in the org-level workspace.

The charter is a directive. It's the CEO's voice addressing the organization. It articulates what the data is, what's already known (so workers don't waste effort rediscovering it), what the organization is hunting for, what good work looks like, what bad work looks like, and what the stakes are. It's written to be read by every worker in the organization and to anchor their judgment of quality throughout the run.

Genesis does not describe the corpus in a dry technical sense — that's raw material that feeds into the charter's framing. Genesis does not design the organization — that's the Planner's job. Genesis does not define areas of investigation — that's also the Planner's job. Genesis sets purpose and standards; everything downstream derives from that.

The charter is authored once at run start and does not change during the run. Every worker reads from the same version.

---

## Planner

Planner reads the charter and produces the operational plan for this particular task. The plan includes two things deposited in the org-level workspace: the rules of engagement, and the initial scopes for the organization.

**What the Planner derives, rather than what it's told to produce:** the Planner's operational plan is a derivation from the charter. What the rules articulate, how the work is divided, what the budget shape looks like, what constraints apply — all of these are the Planner's reasoning from the charter, not prescribed by the design. A charter demanding one kind of work produces one kind of operational plan; a different charter produces a different plan. The design specifies the Planner's job (translate directive into operational reality) and the workspace format (rules of engagement + initial scopes), not the content of those outputs.

**What the rules of engagement cover:** whatever operational behaviors the charter calls for. For a charter demanding exploration, this probably includes budget policy (how much goes to confirming known things versus exploring the unknown), evidence citation expectations, depth policy, decomposition policy, and whatever guardrails are needed to keep the organization faithful to the directive. For a charter demanding different work, the rules cover different territory. The Planner writes what the charter's directive requires.

**Initial scopes:** the Planner produces scopes for the top-level workers of the organization. How many scopes, what they cover, what budget each has — these are derivations from the charter, the corpus shape, and the total budget. Each initial scope is deposited in the org-level workspace as a short definition: what this scope investigates, its budget, its success criteria, references to charter and rules.

**What the Planner doesn't do:** doesn't design roles or cognitive specialization, doesn't paraphrase the charter, doesn't monitor execution. Once scopes are deposited and top-level workers are spawned, the Planner's job is done (it may participate in final organizational synthesis if the design includes that, but it doesn't steer workers mid-run).

---

## Depth policy

Depth in Phase F is not capped globally. Phase A's max_depth parameter that forced shallow trees was installed for budget safety when early runs went to depth 90+ unchecked. Phase F handles depth differently because the budget itself provides the real constraint.

**How depth is managed now:**

The rules of engagement articulate depth policy derived from the charter. For an exploration charter, the rules might say something like "depth is not capped; managers are accountable for justifying continued decomposition; branches where rate of gain has plateaued should be pruned." A different charter would produce different depth guidance.

Managers judge depth locally. A manager at depth 4 deciding whether to decompose further reasons from its own scope, budget, and the charter's standards. Is going deeper here likely to produce something worth the budget? The judgment is the manager's, informed by the charter and rules, not imposed by a global parameter.

Budget is the hard constraint. A worker with a small envelope can't go deep because it runs out of money. A manager allocating envelopes to its children is deciding how deep each child can go by how much it gives them. Responsible allocation means depth-capable envelopes where depth is warranted, and shallow envelopes where it isn't.

**What replaces the global max_depth:** a very permissive upper bound (depth 20 or similar) just to prevent pathological runaway loops from bugs. This is a safety circuit, not a quality control. Quality control is in the rules of engagement and in manager judgment.

**Why this matters for the original novelty problem:** the Federal Register run that produced the novel finding went to depth 90 on the old architecture. Phase A's depth caps cut off that kind of exploration for budget reasons. Phase F restores depth freedom (governed by budget and judgment) so that when the corpus and task warrant going deep, the system can.

---

## Workers

The worker primitive doesn't change structurally. What changes is its relationship to context.

**At spawn, a worker receives:** references to the workspaces relevant to its position in the tree (org-level always, plus any department-level workspaces from root to its parent), plus its own scope, plus a budget envelope, plus any observations passed down from its parent.

**At each turn:** the worker's prompt is kept light. Minimal framing that reminds the worker to consult workspaces for authoritative guidance. Current turn state. Its own scope. Workspace content is read when needed, not injected by default.

**When workspaces get read:** workers read the charter when making quality judgments ("is this observation worth reporting?", "am I meeting the standard?"). Read the rules when deciding how to operate ("can I spawn more children for this?", "what's the policy on this kind of evidence?"). Read their own scope frequently, since the scope defines what success means for this specific worker. Workspace reads are explicit — the worker decides when it needs authoritative guidance, not automatic.

**Resolution vs decomposition:** at resolution (the worker finished its own work without decomposing), it produces final observations, self-evaluates against its scope's success criteria, returns output to parent. At decomposition, it becomes a manager — writes sub-scopes for children, creates a department-level workspace, allocates its budget across children, spawns children, suspends until they return, then synthesizes.

---

## Manager responsibility

When a worker decomposes, it takes on manager identity and does the program-office job for its subtree. This identity shift is part of what makes Phase F's architecture work — managers aren't just "workers with children," they're workers that have accepted responsibility for a department.

**At decomposition:** manager creates a department-level workspace. Writes sub-scopes for children as specializations of its own scope. May write rule addenda (can tighten rules for its subtree, cannot loosen them). Allocates its budget envelope across the children. Spawns children. Suspends.

**At resolution (when children return):** manager reads children's outputs. Synthesizes them into a department report. The department report is the manager's own output to its parent — it's what rolls up the tree. The synthesis preserves specifics from children's observations (named records, exact evidence, specific citations) rather than summarizing them into generic claims. The manager is accountable for this preservation; its department report is evaluated against the charter's standards the same way individual observations are.

**Player-coach:** a manager may have produced observations directly before deciding to decompose. Those observations count as part of the department's work. The manager's synthesis includes its own observations alongside children's.

**Depth of authority:** a manager at depth 4 is the program office for everything in its subtree. Its scope, rule addenda, and department workspace apply to all its descendants. It doesn't have authority over sibling subtrees or over the organization beyond its own branch.

---

## Synthesis as an organic consequence of manager resolution

Today synthesis is a separate pipeline phase at the end of exploration. One synthesizer reads 187 observations and compresses them to 4 findings. That compression ratio is where specifics get lost.

In Phase F, synthesis happens at every manager's resolution. A manager at depth 4 synthesizes its 2-3 children's outputs into a depth-4 department report. A depth-3 manager synthesizes its children (each already a department report) into its own department report. And so on up to the root.

This distributes the compression across layers. A depth-4 manager compresses maybe 6-10 observations into a report. A depth-3 manager compresses 3-5 department reports. Each layer's compression ratio is manageable. Each manager is accountable for preserving what was found in its subtree.

The root-level synthesis — or Genesis stepping in to play that role — produces the final organizational output by combining top-level department reports. This is the one remaining pipeline-level synthesis step, and it operates on already-synthesized inputs rather than on raw observations.

---

## Reader test instrumentation

This is the quality gate that has been in the strategy doc for months and never in code. Phase F instruments it.

At the end of the run, after organizational synthesis produces the final findings, a reader-test pass scores each finding. The scoring is an LLM call with the charter in context: given the charter's standards, would a knowledgeable reader of this corpus say "I didn't know that" about this finding, or would they say "sure, that's known"?

Output is a per-finding score (yes / marginal / no) with reasoning, written into metrics.json alongside other run metrics.

This matters because every phase we've shipped has passed its local exit criteria while finding quality degraded. We measured what was easy (structural metrics, validation rates) and not what mattered (would a knowledgeable reader be impressed). Phase F closes that gap.

The reader test is LLM judgment, so it's imperfect. But it's aligned with the actual goal in a way that no structural metric has been.

---

## What's in scope and what defers

**In scope:**

- Workspace mechanics (creation, population, read access, scope boundaries)
- Genesis producing the charter in directive voice
- Planner deriving operational structure from the charter
- Manager identity and recursive behavior
- Per-manager department synthesis
- Relaxed depth policy with budget as the practical constraint
- Reader-test instrumentation

**Deferred to later phases:**

- Cross-run workspace reuse (the organizational-metadata moat from the vision)
- Mid-run replanning / rate-of-gain reallocation
- Multiple task modes beyond discovery (framework generalizes; Phase F instantiates discovery only)
- Strategic dormancy and reactivation
- AutoResearch-style prompt fine-tuning (see future enhancements)

---

## Future enhancements

**AutoResearch-style prompt fine-tuning.** Karpathy's AutoResearch pattern — a coding agent reading a spec, running experiments, keeping what improves, rolling back what doesn't — could be applied to Mycelium's own prompts. The Genesis prompt, Planner prompt, worker prompts, synthesis prompts all have room to improve, and the improvements are hard to reason about analytically but could be discovered experimentally. After Phase F lands and the reader-test gate is instrumented, the reader-test score becomes a natural fitness function for prompt evolution. A later phase could run AutoResearch on Mycelium's own prompt files with reader-test pass rate as the signal, evolving better prompts over iterations.

Not Phase F work. Flagged here so the capability isn't forgotten.

---

## Integration with what exists

AnalyticalSurvey: unchanged. Feeds Genesis (informing the charter's "already known" content) and Planner (informing what validation-of-known-things might look like, if the Planner's derivation produces that).

Briefing system: feeds Genesis. Becomes raw material for the charter's directive voice.

Budget pool: unchanged structurally. Workers spend from it, managers allocate from it. Workspaces describe policy; the pool enforces spending.

Envelope discipline: the rules of engagement articulate envelope policy where relevant; the pool enforces.

Validator: extended to read the charter when evaluating findings against standards.

Significance gate: extended similarly. The charter defines what significance means for this run.

Pipeline structure: unchanged. Genesis → Planner → initial spawn → recursive investigation with in-tree synthesis → final organizational synthesis → reader-test scoring.

---

## Testing and evaluation

**Primary test:** on npm and SEC corpora with the same $10 budgets as the pre-Phase-E baseline runs (npm 18435eac, SEC f576e964), does Phase F produce findings that pass the reader test that the baseline findings did not?

**Minimum pass criterion:** on at least one corpus, at least three findings pass the reader test (yes, not marginal).

**Secondary tests:**

- Does Genesis produce a charter that reads as a directive rather than a description?
- Does the Planner's operational plan feel derived from the charter rather than templated?
- Do workers reach meaningful depths when the work warrants it, now that depth isn't globally capped?
- Does per-manager synthesis preserve specifics that single-end-of-run synthesis would lose?
- Does the reader-test score correlate with human judgment of the findings?

---

## Open questions before build

**Workspace infrastructure.** What's the actual substrate for shared workspaces? Filesystem with explicit read calls from workers? In-memory shared state? Prompt caching where identical prefix across calls is charged once? The choice affects cost and latency. Probably filesystem paths with explicit reads for the first implementation, with caching as a later optimization.

**Workspace read discipline.** How do we make workers disciplined about when to read the charter vs operating from scope alone? Probably a combination of worker-prompt structure (reminders about when charter-consultation is warranted) and the rules of engagement (explicit policy on when consultation is required). Worth tuning after first runs.

**Revision semantics.** When a manager revises a child's sub-scope mid-run, what happens to in-flight work? Likely the child is recalled and re-briefed. But details matter for not breaking in-flight investigations.

**Genesis writing in voice.** Generating a directive consistently in CEO voice is a prompt-engineering challenge. The charter has to incorporate what the survey found (the "already known" content) while staying alive as a directive rather than becoming a report. This is where AutoResearch-style prompt evolution would eventually help; for Phase F, it's a careful manual prompt.

**Budget for document generation.** Charter, rules, and initial scopes cost tokens to produce. If this runs over 10% of total budget, it eats into exploration. Initial estimate: 5-10% total. Worth measuring in first runs.

**Cost of workspace reads during exploration.** If workers read charter content more often than expected, total cost climbs. If they read too rarely, fidelity erodes because they're operating from memory of scope alone. The right frequency is an empirical question.

---

## What this isn't

**Not role specialization.** There are no predefined role types. No "Structural Analyst" or "Comparative Analyst" categories. Workers approach their scopes based on what the scope demands, and managers write sub-scopes that implicitly define what each child investigates. "Roles" in the cognitive-mode sense are emergent from well-written scopes, not schema-level entities.

**Not free compression.** Workspace reads cost tokens. The value of workspaces is fidelity (one source of truth, no paraphrase drift) and structural compression (read-when-needed rather than inject-by-default). Not zero-cost content.

**Not the full vision.** The vision describes Genesis reasoning about what kind of organization any task requires. Phase F is that mechanism for discovery tasks only. Other task modes (risk audit, code build, strategy) would use the same framework with different charters and rules, but Phase F doesn't build those.

**Not a fix for all the quality problems.** Phase F addresses fidelity, specificity preservation, and the reader-test gate. Convergence (the system rediscovering the same findings) is partially addressed by different charters producing different operational plans, but may need further work in later phases (exploration diversity mechanisms, hypothesis generation, entry-mode flexibility beyond what Planner-derived rules provide).
