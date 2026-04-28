# Mycelium: Product Requirements Document & Operational Manual

**Version:** 1.0
**Date:** 2026-04-25
**Author:** Anurag Mohanty
**Status:** Living document

---

# PART 1: PRODUCT REQUIREMENTS DOCUMENT

---

## 1. What Mycelium Is

Mycelium is an autonomous discovery framework that finds unknown patterns in any information space. You point it at a data source, give it a dollar budget, and it decides what to explore, how deep to go, and what to report. No human guides the investigation after launch. The system decides its own organizational structure, hires its own specialists, evaluates their work, and synthesizes findings into a validated report.

### 1.1 The Core Concept

Traditional data analysis requires a human to know what questions to ask. Mycelium inverts this: it discovers the questions worth asking, then answers them. The output is not a response to a query but an autonomous exploration report with tiered findings, each backed by specific evidence, validated by a skeptical reviewer, scored for novelty, and assessed for real-world impact.

The system operates like a research organization. It begins with a structural survey of the data (zero LLM cost), writes an organizational charter setting standards and stakes, hires investigators, reviews their work against authored success bars, synthesizes cross-cutting patterns, and produces a final report. Every finding traces back to specific data points with citations.

### 1.2 The Core Principle: Code Is Plumbing, LLM Is the Brain

Nothing in the Python code is specific to any data corpus. There is no npm-specific logic in prompts, no field-name matching in the survey engine, no domain knowledge in the orchestrator. The system works identically whether pointed at:

- npm registry (100K+ software packages)
- SEC EDGAR (41K+ 10-K financial filings)
- Hospital patient records (FHIR/HL7)
- Pollution monitoring stations
- A company's Jira tickets
- US Federal Register regulations

**The only things that change between data sources:**

1. The connector (how to fetch records)
2. The data itself (what fields exist)

**The things that NEVER change:**

- Prompts (they reason about whatever data they receive)
- Survey engine (pure statistics on any list of dicts)
- Exploration logic (recursive decomposition via role-authoring)
- Synthesis, validation, significance, impact pipeline
- Visualizer and event system

If you find yourself writing `if field == "downloads"` or `if source == "npm"` anywhere except inside a data source connector, you are violating this principle.

### 1.3 Demonstrated Capability

Mycelium has been demonstrated on:

| Data Source | Records | Typical Budget | Typical Findings |
|---|---|---|---|
| npm registry | 100K+ packages | $5-$50 | Supply-chain risks, maintainer concentration, dependency anomalies |
| SEC EDGAR | 41K+ 10-K filings | $5-$50 | Regulatory disclosure patterns, risk-factor evolution, cross-company contradictions |
| Federal Register | Public regulations | $5-$50 | Regulatory patterns, agency behavior anomalies |

### 1.4 Cost Model

- **Model:** Claude Sonnet 4 (`claude-sonnet-4-20250514`)
- **Pricing:** $3/M input tokens, $15/M output tokens
- **Extended thinking:** 8000 token budget per LLM call
- **Typical $5 run:** ~30 nodes, ~60 observations, depth 3-4
- **Typical $10 run:** ~45 nodes, ~180 observations, depth 4+
- **Typical $50 run:** ~200+ nodes, ~500+ observations, depth 5-6

---

## 2. Architecture Overview

### 2.1 The Full Pipeline

```
Catalog (free) --> Briefing --> Genesis --> Workspace Setup --> Exploration --> Synthesis
     |                |            |              |                 |              |
  Statistical     Common        Charter      charter.md +     Role-authoring   Cross-
  survey of      knowledge     generation    rules.md          tree with       referencing
  bulk data      baseline                                     Turn 2 review    findings
     
--> Deep-Dive --> Validation --> Significance --> Impact --> Report --> Reader Test
       |              |               |              |          |            |
   Targeted       Skeptical        Novelty +      Real-world  Five-tier   Per-finding
   follow-up      review          actionability   consequences markdown    scoring
   on top         (factual vs     scoring         assessment   report     against
   findings       interpretive)                                           charter
```

Each step is a separate module. The orchestrator (`orchestrator.py`) coordinates the pipeline but makes no exploration decisions. All reasoning is delegated to the LLM via prompts defined in `prompts_v2.py`.

### 2.2 The Unified Node Primitive

Every exploration node in Mycelium is a `RoleWorkerNode` (defined in `worker_v2.py`). There is no separate "manager node" or "worker node" class. Every node receives the same inputs, runs the same formation-time assessment, and can become either an investigator or a manager based on its own reasoning.

The distinction between "manager" and "worker" is **descriptive, not prescriptive**:

- A node that decides to investigate directly is a **worker** for this engagement
- A node that decides to hire specialists is a **manager** for this engagement
- A node can start as a worker and become a manager mid-investigation (via reassessment)
- The code path is identical; the LLM's formation assessment determines the behavior

### 2.3 Budget as the Resource Constraint

Budget is the fundamental constraint governing every decision in the system. It is not a soft guideline; it is a hard limit enforced at every level:

- **BudgetPool** (`schemas.py`): A shared atomic budget tracker that prevents parallel overspend
- **Phase limits**: Hard caps on how much each pipeline phase can consume
- **Per-node envelopes**: Each node receives a budget allocation from its parent
- **Minimum viable envelope**: $0.12 -- the minimum budget a node needs to do productive work (roughly one LLM call with reduced thinking)
- **Exploration hard cap**: 85% of total budget (in the role-authoring path)

---

## 3. The Node Lifecycle

This is the most important section of this document. Every finding Mycelium produces originates from a node's investigation. Understanding the node lifecycle is understanding how the system works.

### 3.1 Formation-Time Assessment

When a node is spawned, the first thing it does is assess whether it should do the work itself or hire specialists. This happens in a single LLM call that also fetches and analyzes data.

The node receives:

- **Role definition**: name, mission, bar, heuristic (authored by its parent)
- **Scope description**: what territory this node is responsible for
- **Purpose**: why this node was hired -- what its parent needs from it
- **Parent context**: evidence or reasoning from the parent that motivated this hire
- **Workspace context**: the organizational charter and rules of engagement
- **Data**: up to 100 records fetched from the data source using the node's assigned filter
- **Budget information**: allocated envelope, parent pool remaining, phase remaining
- **Filter schema**: what query parameters the data source accepts

The formation assessment runs two tests:

#### 3.1.1 Floor Test

> Is delegation overhead (authoring roles + briefing + reviewing) less than doing the work directly?

If the work itself costs less than the overhead of setting up hires to succeed, the node should do it itself. Slicing the same kind of analysis into smaller pieces is fragmentation, not delegation.

**Concrete example:** A node with $0.30 budget examining 50 records about a specific topic. Authoring 3 hire roles, briefing each, and reviewing their output would cost ~$0.20 in overhead alone. The node should investigate directly.

#### 3.1.2 Ceiling Test

> Can you hold this scope at your bar's required depth in one careful pass?

The node reasons concretely: how many items are in scope? What depth of analysis does the bar require -- per-item, cross-item, or both? How much context is consumed by role definition and parent context, leaving how much for actual data? If the scope exceeds what one careful pass can cover at the depth the bar demands, hiring is warranted.

**Concrete example:** A node with $2.00 budget responsible for "all technology sector filings" covering 5,000+ records across multiple industries. One pass cannot analyze 5,000 records at per-item depth. The ceiling test fails; hiring is warranted.

**Both tests must pass to justify hiring.** The formation assessment must include a reasoning trace grounding the decision in observable inputs: specific scope size, specific bar requirements, specific capacity estimate.

### 3.2 Role Definition

Every node receives a role definition authored by its parent (or by the system for the engagement lead). A role definition has four fields:

| Field | Purpose | Example |
|---|---|---|
| **name** | What this role is called -- captures the kind of cognition, not a topic label | "supply-chain risk analyst" not "npm security" |
| **mission** | What excellent work looks like -- the aspiration, not a checkbox | "Produce the definitive map of hidden single-maintainer dependencies in the most-downloaded packages, with blast-radius estimates for each" |
| **bar** (success_bar) | Minimum acceptable output -- below this is failure. Concrete enough to fail against. | "Name at least 3 specific packages with >1M weekly downloads maintained by a single person, with dependency counts and downstream impact for each" |
| **heuristic** | Posture for ambiguous moments during investigation | "When uncertain whether a pattern is a data artifact or genuine signal, investigate the artifact hypothesis first -- if it's real, it saves time; if it's an artifact, you've saved a false finding" |

**Mission vs Bar:** The mission is what the node aspires to. The bar is the floor. Workers reason against the mission (trying to produce their best work), then check against the bar (catching failure). Meeting the bar means the node hasn't failed; it does not mean the node has done its best work.

### 3.3 The Investigation Path

When a node decides to investigate directly (floor and ceiling tests both pass), it follows this path:

```
Fetch Data (up to 100 records via data source filter)
    |
    v
STEP 1: SURVEY -- Inventory what's in front of you
    |
    v
STEP 2: ORIENT -- Read carefully, find patterns, surprises, absences
    |
    v  
STEP 3: HYPOTHESIZE -- Form specific hypotheses with evidence
    |
    v
STEP 4: ASSESS COVERAGE -- Self-evaluate honesty
    |
    v
SURFACE AND COMMIT -- For each observation, name tensions, commit to action
    |
    v
STEP 5: OUTPUT -- Produce evidence packets (observations)
    |
    v
REASSESSMENT -- Re-run floor/ceiling tests with new information
    |
    +--> RESOLVE (work meets mission standard or budget < $0.15)
    +--> INVESTIGATE FURTHER (extend with another reasoning turn)
    +--> HIRE (scope has dimensions requiring distinct cognition)
```

#### 3.3.1 Surface and Commit

Before any observation goes into the output, the node walks it through two steps:

**SURFACE:** Name every tension noticed about this observation during reasoning. Tensions are things that gave pause -- anything where what is about to be reported sits uncomfortably against the directive, the evidence, or the node's own assessment.

**COMMIT:** For each tension surfaced, state on the record what is being done about it and why. This is a commitment, not a reflection. The node binds itself to an action: drop this observation, revise it to resolve the tension, or keep it with an explicit statement of why the tension does not invalidate it.

The test: after the commit, would the node defend this observation to the person who wrote its directive?

#### 3.3.2 Evidence Packets (Observations)

Every observation a node produces is an evidence packet with required fields:

```json
{
    "raw_evidence": "THE SPECIFIC DATA -- actual values, numbers, text from records",
    "statistical_grounding": "which survey techniques flagged this (z-score, cosine similarity, etc.)",
    "local_hypothesis": "a SPECIFIC, non-generic explanation of why this anomaly exists",
    "source": {
        "doc_id": "unique identifier",
        "title": "name or title of the item",
        "agency": "author, publisher, maintainer",
        "date": "relevant date",
        "section": "specific section if applicable",
        "url": "URL to the source"
    },
    "observation_type": "pattern | anomaly | absence | temporal_shift | concentration | ...",
    "confidence": 0.85,
    "confidence_rationale": "why this confident -- name uncertainties",
    "signal_strength": "data_originated_novel | data_originated_confirmatory | confirmatory",
    "surprising_because": "what you would EXPECT to see and how this differs"
}
```

**Signal strength** has three levels:

| Level | Meaning |
|---|---|
| `data_originated_novel` | Required the data AND not covered by the briefing |
| `data_originated_confirmatory` | Required the data BUT restates something in the briefing |
| `confirmatory` | Would be expected without reading the data at all |

**Integrity rules:**

- Every observation MUST cite specific data with its identifier
- `raw_evidence` must contain ACTUAL VALUES from the records -- numbers, text, dates -- not descriptions or summaries
- If an observation could be written without looking at the data, it is NOT a finding
- Never spawn exactly one child -- either resolve or create 2+

### 3.4 The Manager Path

When a node decides to hire (floor test fails or ceiling test fails), it becomes a manager:

```
Formation Assessment --> decision: "hire"
    |
    v
STEP 3: DESIGN YOUR TEAM
    |
    For each hire:
    |   A. Role name (kind of cognition, not topic label)
    |   B. Justification (why distinct cognition is needed)
    |   C. Mission (what excellent work looks like)
    |   D. Bar (minimum acceptable, concrete enough to fail against)
    |   E. Heuristic (posture for ambiguous decisions)
    |   F. Data filter (DIFFERENT filter for DIFFERENT records)
    |   G. Scope description (grounded to data assignment)
    |   H. Budget (matched to scope complexity)
    |
    Also: Author a SYNTHESIS ROLE for cross-referencing
    |
    v
Spawn hire nodes --> Run in parallel
    |
    v
Wait for all hires to return
    |
    v
TURN 2: Evaluate each hire against authored bar
```

#### 3.4.1 Data Partitioning (MECE)

This is critical: each hire MUST receive a different partition — a filter condition over record fields that selects a non-overlapping slice of the corpus. The engagement lead's primary job is authoring partitions that tile the corpus, not choosing what to investigate.

Partitions are defined by record attributes: `maintainer_count = 1`, `dependency_count >= 6 AND dependency_count <= 25`, `created < '2020-01-01'`. The EQUIP workspace provides field distributions so the engagement lead can pick natural break points.

The MECE partition gate verifies every partition set before children execute. If partitions overlap, leave gaps, or can't be translated to filters, the run halts.

#### 3.4.2 Budget Allocation to Hires

The manager reserves ~20% of its remaining budget for Turn 2 review, then distributes the rest across hires. Each hire must receive at least $0.12 (the minimum viable envelope). The system automatically rejects spawns below this minimum.

The manager is also instructed: before allocating, check whether the scope actually warrants the budget. Giving $1 of budget for a single specific question that a worker can answer in one pass leads to under-utilization.

### 3.5 Turn 2: Evaluating Hires

After all hires return, the manager runs a Turn 2 evaluation. This is a separate LLM call using `MANAGER_TURN2_PROMPT_V2`. The manager evaluates each hire on two independent dimensions:

#### 3.5.1 Dimension A: Bar Compliance

The manager quotes the bar it authored, identifies specific elements in the hire's output that address or fail to address each requirement, element by element.

| Classification | Meaning |
|---|---|
| **MET** | Each bar element has a corresponding output element that clears it |
| **POOR_REASONING** | Specific bar elements are unmet, and the hire had the data to meet them. The manager names which elements and what was produced instead |
| **WRONG_ROLE** | The hire produced reasonable work that doesn't map to the bar, because the bar was misaligned with the territory |

A classification without specific element-to-element reasoning is not trustworthy. Default to MET if the manager cannot evaluate concretely.

#### 3.5.2 Dimension B: Commitment

Independent of whether the hire met the bar. Evaluates whether the hire pushed as hard as it should have given its mission and resources.

| Classification | Meaning |
|---|---|
| **COMMITTED** | The hire used its budget meaningfully. Any remaining unused budget could not support meaningful additional investigation. Any flagged threads are marginal relative to what was already produced |
| **UNDERFIRED** | The hire has substantial unused budget that could fund meaningful additional work, AND identifiable threads that would add substantive value, AND the hire resolved anyway. The manager must cite specific unused budget amount, specific threads not pursued, and why those threads would be substantive |
| **OVERFIRED** | Continued past where it should have. Diminishing returns, threads were genuinely repetitive, budget was consumed on redundant work |

A hire can be MET-bar AND UNDERFIRED. Meeting the minimum does not mean the hire committed fully to its mission.

#### 3.5.3 Continuation Decisions

After evaluating hires, the manager decides what to do with remaining budget using concrete arithmetic:

1. **How much budget remains** after reserving for downstream phases?
2. **How many continuation hires** could that fund (based on observable average cost per hire)?
3. **For each candidate thread:** what analytical method did the prior hire use, and what different method would investigate further?

| Action | When | Requirements |
|---|---|---|
| **CONTINUE** | Arithmetic shows budget AND threads with different methods exist | Continuation directives with new roles |
| **REHIRE** | A POOR_REASONING or WRONG_ROLE hire left valuable territory uncovered | Revised role definition |
| **RESOLVE** | Arithmetic shows continuation is not warranted | Explicit justification |

Continuation directives spawn new nodes that pick up where previous hires left off, receiving the original hire's observations as context.

### 3.6 Mid-Investigation Reassessment

After a worker produces initial observations, it doesn't immediately resolve. Instead, it runs a **reassessment** (`WORKER_REASSESSMENT_PROMPT_V2`) -- the same floor/ceiling tests from formation, but now with the information gained from actual investigation.

The reassessment asks:

1. **What changed?** At formation, scope, capacity, and bar depth were assessed with limited information. Now that investigation is complete, what do observations reveal that wasn't known then?
2. **Floor test (re-run):** Given what is now known, would delegation overhead exceed doing the work directly?
3. **Ceiling test (re-run):** Given what is now known, can remaining scope be covered at the depth the mission requires within one more reasoning pass?
4. **Mission check:** Do current observations meet the mission's standard (not just the bar)?

| Outcome | Condition |
|---|---|
| **RESOLVE** | Work meets mission standard, or remaining budget < $0.15 |
| **INVESTIGATE_FURTHER** | Both tests pass but mission warrants more depth. Take another reasoning turn |
| **HIRE** | Floor test now fails (scope has distinct dimensions) or ceiling test now fails (scope exceeds capacity). Requires at least $0.24 remaining (2x minimum envelope) |

When a worker extends (INVESTIGATE_FURTHER), it runs `WORKER_EXTENSION_PROMPT_V2` to push its initial observations deeper. Each extended observation must add substantive new evidence or analysis that wasn't in the initial pass -- not restatements.

When a worker transitions to manager (HIRE), it uses the same role-authoring machinery to design a team for the remaining work. Its initial observations are preserved as the manager's own findings.

### 3.7 Chain Circuit Breaker

The ONE hardcoded safety check: `MAX_CHAIN_DEPTH = 8`. If a node is 8 levels deep in a single-child chain (each parent spawned only one child), the system forces it to resolve directly. This prevents infinite decomposition loops.

When the circuit breaker fires, the node receives `NODE_FORCE_RESOLVE_OVERRIDE_V2`:

> "OVERRIDE: You are deep in a single-hire chain (N levels). You MUST investigate directly. Do not hire. Set hire_directives to empty. Produce observations from the data in front of you."

---

## 4. Data Flow

### 4.1 Catalog: AnalyticalSurvey

The first step is a pure-Python statistical analysis of the entire corpus. Zero LLM cost.

**Input:** All accessible records via `fetch_bulk_metadata()` (up to 2,000 records)

**Techniques (8 independent analyses):**

| # | Technique | What It Finds |
|---|---|---|
| 1 | Basic statistics | Distributions, z-score outliers, concentration metrics |
| 2 | Isolation Forest | Multi-dimensional outlier detection |
| 3 | TF-IDF text analysis | Unusual text content across records |
| 4 | DBSCAN clustering | Records that don't belong to any cluster |
| 5 | Entity concentration | Entities with outsized influence |
| 6 | Graph analysis | Centrality, inversions, orphan nodes |
| 7 | Temporal analysis | Stale-but-active records, velocity anomalies |
| 8 | Keyword signals | Uncommon keywords correlating with extreme values |

**Output:** Per-technique anomalies and multi-flagged records (records flagged by multiple techniques are highest-priority investigation targets). Results are cached to `catalog/survey_cache_{hash}.json` to avoid re-running on unchanged data.

All analysis is domain-agnostic. The same code runs on npm packages, SEC filings, hospital records, or any other list of dicts.

### 4.2 Briefing (Common Knowledge Baseline)

Before exploration, the system generates a common knowledge briefing -- what a domain practitioner already knows about this space. This serves as a novelty calibration baseline: findings that merely restate common knowledge are filtered out.

The briefing is cached per data source to `catalog/briefings/briefing_{source}.md`. If a cached briefing exists, it is loaded without LLM cost.

### 4.3 Genesis: Charter Generation

Genesis reads the corpus metadata, survey results, and briefing. It produces an **organizational charter** -- a directive written in CEO voice that sets purpose, standards, and stakes for the entire investigation.

The charter covers:

1. **What we are investigating and why it matters** -- framing the corpus and mission
2. **What is already known** -- categories of known patterns from the briefing, structured so workers can recognize when their finding matches a known shape
3. **What impresses us and what doesn't** -- the quality bar with concrete examples
4. **What the stakes are** -- why the investigation matters

The charter is written in directive voice, under 800 words, and is read by every worker before every quality decision.

**Prompt used:** `CHARTER_PROMPT`

### 4.4 Workspace: charter.md and rules.md

After genesis, the orchestrator creates an organizational workspace directory containing:

- **`charter.md`**: The organizational charter from genesis
- **`rules.md`**: Rules of engagement derived from the charter by the operational plan

Every node in the exploration tree reads these files as part of its workspace context. This ensures consistent quality standards across all depths.

### 4.5 Operational Plan

The operational plan (`OPERATIONAL_PLAN_PROMPT`) translates the charter into operational reality:

- **Rules of engagement**: Specific policies governing how every worker behaves -- budget policy, evidence standards, depth policy, quality bar, novelty requirements
- **Initial scopes**: The first division of the investigation into distinct areas, each with scope level classification (manager/worker/ambiguous), charter rationale, budget allocation, and success criteria
- **Budget allocation**: How total budget is split across investigation, synthesis, validation, impact analysis, report generation, and overhead. All allocations are **ceilings, not reservations** -- unused budget flows back to the shared pool

### 4.6 Engagement Lead

The first node in the exploration tree is the **engagement lead**. It has a system-authored role:

- **Name:** "engagement lead"
- **Mission:** "Produce the most insightful investigation this budget can yield. Build a team that uncovers what nobody else has noticed..."
- **Bar:** "Design and staff an organization that produces findings meeting the charter's standards..."
- **Heuristic:** "When uncertain whether to investigate directly or hire, ask: does this scope require distinct kinds of work that a single pass cannot cover?"

The engagement lead receives the entire charter as its scope description and the full exploration budget (85% of total). It is expected to hire, not investigate directly, because the scope is too broad for one pass.

### 4.7 Data Partitioning (MECE)

The engagement lead partitions the corpus into non-overlapping slices that together cover every record. Each hire receives a different partition — a filter condition over record fields.

**Partitions** are data slices defined by field values:
- `dependency_count = 0` (38,056 records)
- `dependency_count >= 1 AND dependency_count <= 5` (47,734 records)
- `dependency_count >= 6 AND dependency_count <= 25` (13,406 records)
- `dependency_count >= 26` (1,530 records)

These tile the corpus: every record is in exactly one partition.

**Lenses** are analytical questions (NOT partitions):
- "coordination patterns" — applies to any record, can't be filtered
- "temporal anomalies" — analytical concept, not a data attribute

The engagement lead authors partitions. Workers apply lenses within their partition.

### 4.7.1 EQUIP Workspace Prep

Before exploration, EQUIP analyzes the catalog and writes a SKILL.md containing:
- **Schema** — field names, types, ranges, sample values
- **Partitioning Guide** — field distributions with percentiles and segment counts at natural break points, plus 3-5 ready-to-use partition schemes
- **Partition Rules** — explicit distinction between partitions (filter conditions) and lenses (analytical questions)

The engagement lead reads SKILL.md and uses the field distributions to choose partition dimensions and break points.

### 4.7.2 EQUIP Translator

The translator converts natural-language partition descriptions to SQL:

1. **Author SQL** — LLM generates SQL from partition description + schema context
2. **Schema check** — validates all column names exist
3. **Execute** — runs SQL against the enriched catalog SQLite DB
4. **Return** — records + SQL + interpretation (no value revision)

The translator is pure plumbing. It does not revise filter values based on result count. If a partition returns 0 records, that's information for the engagement lead, not a problem for the translator to fix.

### 4.7.3 MECE Partition Gate

At every parent→child boundary where a node hires, the partition gate verifies:

1. **Shape check** — each child's partition translates to SQL (not a lens)
2. **Exclusivity check** — no two children's record sets overlap
3. **Completeness check** — union of children covers the parent's scope

If any check fails and `--partition-gate on` (default), the run halts with a diagnostic at `diagnostics/partition_gate/{node_id}.json`. The diagnostic names which check failed, by how much, and gives examples.

The gate fires recursively — if a worker sub-partitions its slice, the gate enforces MECE at that boundary too.

### 4.8 Observations: Evidence Packets

All observations flow upward through the tree:

```
Leaf workers produce observations
    |
    v
Parent managers review via Turn 2
    |
    v
Synthesizer cross-references across siblings
    |
    v
Validator challenges each finding skeptically
    |
    v
Significance gate scores novelty + actionability
    |
    v
Impact analyzer assesses real-world consequences
    |
    v
Reporter compiles five-tier markdown report
    |
    v
Reader test scores against charter standards
```

### 4.9 Findings Flow

| Stage | Input | Output | Quality Gate |
|---|---|---|---|
| Exploration | Raw data | Evidence packets (observations) | Surface-and-commit, bar check |
| Synthesis | All observations from siblings | Reinforced patterns, contradictions, cross-cutting patterns | Citation discipline |
| Validation | Each Tier 3-5 finding | Verdict: confirmed / confirmed_with_caveats / weakened / refuted | Factual + interpretive assessment |
| Significance | Validated findings | Novelty + actionability score, tier assignment | Score >= 3.0 to proceed |
| Impact | Significant findings | Affected parties, scale, financial exposure, urgency | Specificity check |
| Report | All validated, significant, impactful findings | Five-tier markdown report | Tier-appropriate citations |
| Reader test | Report findings + charter | Per-finding score: yes / marginal / no | Factual novelty + interpretive certainty |

---

## 5. Budget System

### 5.1 BudgetPool

The `BudgetPool` (defined in `schemas.py`) is a shared atomic budget tracker. It prevents parallel overspend through lock-based reservation.

```python
class BudgetPool:
    def __init__(self, total_budget: float):
        self.total = total_budget
        self.spent = 0.0
        self.reserved = 0.0  # money committed but not yet spent
        self._lock = asyncio.Lock()
```

**Atomic reservation flow for parallel exploration:**

1. **Reserve:** Before starting work, a node atomically reserves estimated cost. Returns False if insufficient budget.
2. **Commit:** After work completes, commit actual cost and release unused reservation.
3. **Release:** Cancel a reservation if work was skipped.

### 5.2 Phase Limits

Budget is divided across phases. Exploration and review have **hard caps**; others are soft limits:

| Phase | Allocation | Type | Purpose |
|---|---|---|---|
| Exploration | 85% | Hard cap (40% for v1 path) | Node investigation and hiring |
| Review | 15% | Hard cap | Parent Turn 2 reasoning |
| Synthesis | 13% | Soft | Cross-referencing findings |
| Deep dive | 8% | Soft | Targeted follow-up on top findings |
| Validation | 7% | Soft | Skeptical review of Tier 3-5 |
| Impact | 10% | Soft | Real-world impact assessment |
| Overhead | 7% | Soft | Genesis, planner, catalog translation, report |

**All downstream allocations are ceilings, not reservations.** Unused budget flows back to the shared pool and becomes available for exploration. The pool enforces actual spending; allocations are upper-bound estimates.

### 5.3 Per-Node Envelope

Each node receives a budget allocation from its parent:

- **Engagement lead:** Receives 85% of total budget
- **Manager hires:** Manager reserves ~20% for Turn 2, distributes ~80% across hires
- **Minimum viable envelope:** $0.12 -- nodes receiving less are rejected at spawn time
- **Envelope exhaustion:** When `envelope - spent < $0.03`, the node stops making LLM calls

### 5.4 Budget Arithmetic at Turn 2

When a manager evaluates its hires, it has access to observable cost data:

```
OBSERVABLE COST DATA:
  Your formation cost: $0.045
  Hires completed: 3
  Average cost per hire: $0.182
  Total spent on hires: $0.547
  Downstream phases estimate: $0.50
  Budget after downstream: $0.23 available for continuation
```

The manager uses this to compute:

1. Remaining budget after downstream reservation
2. How many continuation hires that funds (at the observed average cost)
3. Whether each candidate thread requires different cognition from what was already applied

### 5.5 Budget Utilization

**What good looks like:**

- 80-95% of total budget spent
- Exploration phase uses 70-85% of its cap
- No individual node spends more than 5x the average
- Zero-observation nodes < 10% of total nodes
- Surplus returned from resolved nodes is redeployed to continuations

**What waste looks like:**

- Budget returned unused because nodes resolved early without producing findings
- Multiple nodes examining the same records (duplicate data filters)
- Continuation budget available but manager resolved without deploying it
- Nodes allocated $1.00 but spending $0.15 because scope only needed one pass

---

## 6. Quality System

### 6.1 Charter Compliance

The charter defines what "good work" looks like for the entire investigation. Every worker reads the charter as part of its workspace context. Findings must meet the charter's standards for specificity, novelty, and evidence quality.

### 6.2 Bar Evaluation

Every finding is checked against the authored bar at multiple levels:

1. **Worker self-check:** Before producing output, the worker checks observations against its bar
2. **Manager Turn 2:** The manager evaluates each hire's output against the bar it authored
3. **Bar compliance classification:** MET / POOR_REASONING / WRONG_ROLE with element-by-element reasoning

### 6.3 Reader Test

After the report is generated, the reader test scores each finding on two dimensions:

**Factual Novelty:** Would a knowledgeable practitioner say "I didn't know that fact"?

| Score | Meaning |
|---|---|
| YES | The underlying factual observation is something a practitioner would not already know |
| MARGINAL | Partially known or suspected but now quantified with specific evidence |
| NO | Restates something practitioners already know |

**Interpretive Certainty:** How strongly does the interpretation follow from the facts?

| Score | Meaning |
|---|---|
| HIGH | Well-supported by cited evidence, few alternative explanations |
| MEDIUM | Plausible given evidence but other explanations could fit |
| LOW | Large leap from cited evidence, speculative |

**Combined Score:**

| Combined | Condition |
|---|---|
| YES | factual_novelty=YES AND interpretive_certainty=HIGH or MEDIUM |
| YES_FACTUAL | factual_novelty=YES BUT interpretive_certainty=LOW |
| MARGINAL | factual_novelty=MARGINAL, or YES with LOW certainty |
| NO | factual_novelty=NO regardless of interpretation |

### 6.4 Validation

Each Tier 3-5 finding goes through skeptical review. The validator separates factual claims from interpretive claims and evaluates them independently:

**Factual claims:** Observations about the data -- counts, entities, dates, word-level differences. Verifiable: either the data says what the finding claims, or it doesn't.

**Interpretive claims:** What the facts imply -- causal explanations, risk assessments, motivations. Often cannot be fully verified from data alone.

| Verdict | Condition |
|---|---|
| **CONFIRMED** | Factual claims CONFIRMED AND interpretive claims WELL_SUPPORTED |
| **CONFIRMED_WITH_CAVEATS** | Factual claims CONFIRMED but interpretive claims only PLAUSIBLE or SPECULATIVE |
| **WEAKENED** | Factual claims have MISSING_EVIDENCE in part, but partial support exists |
| **REFUTED** | Factual claims REFUTED or evidence directly contradicts the finding |
| **NEEDS_VERIFICATION** | Factual claims themselves cannot be assessed |

The validator also checks for **pipeline issues** -- findings that are about the data collection process rather than the corpus itself. These are separated into a distinct section of the report.

### 6.5 Significance Gate

The significance gate is a "so what?" filter. It prevents obvious, known, or unactionable findings from getting expensive impact analysis.

Each finding is scored on:

- **Novelty (1-5):** Relative to the briefing and practitioner knowledge
  - 1 = Directly covered by briefing
  - 3 = Known vaguely but NOW QUANTIFIED with specific names and numbers
  - 5 = Nobody has reported this publicly before
- **Actionability (1-5):** Can a practitioner DO something with this?
  - 1 = No realistic action
  - 3 = A specific investigation or audit could be triggered
  - 5 = Immediate specific action someone should take RIGHT NOW

**Composite score** = average of novelty + actionability. Genuine must be yes, else 0.

| Tier | Score | Treatment |
|---|---|---|
| HEADLINE | 4.0+ | Top of report, full impact analysis |
| SIGNIFICANT | 3.0-3.9 | Prominent in report, impact analysis |
| NOTED | Below 3.0 | Listed briefly, no impact analysis |

### 6.6 The Quality Pipeline

```
Observation (evidence packet with raw data)
    |
    v
Synthesis (cross-referencing: reinforced? contradicted? connected?)
    |
    v
Validation (factual assessment + interpretive assessment + pipeline issue check)
    |
    v
Significance Gate (novelty + actionability scoring, tier assignment)
    |
    v
Impact Analysis (affected parties, scale, financial exposure, urgency)
    |
    v
Report (five-tier structured output with citations)
    |
    v
Reader Test (per-finding scoring against charter standards)
```

---

# PART 2: OPERATIONAL MANUAL

---

## 7. Every Pipeline Step

### 7.1 Step 1: Catalog (AnalyticalSurvey)

**What happens mechanically:**
The orchestrator calls `data_source.fetch_bulk_metadata(max_records=2000)` to retrieve all accessible records. These records are passed to `AnalyticalSurvey.analyze()`, which runs 8 independent statistical techniques. Results are cached to disk keyed by a hash of record count and boundary record IDs.

**LLM prompt used:** None -- this is pure Python statistical analysis. One cheap LLM call is used afterward to translate raw cluster names into plain language (`_translate_catalog`).

**Inputs:**
- Raw records from the data source (up to 2,000)

**Outputs:**
- `record_count`: Total records analyzed
- `techniques_applied`: Which techniques ran successfully
- `anomalies_by_technique`: Per-technique anomaly lists
- `multi_flagged`: Records flagged by multiple techniques (highest priority)
- `anomaly_clusters`: Grouped patterns of anomalies
- `outliers`, `concentrations`, `distributions`, `correlations`

**Working correctly looks like:**
- 6-8 techniques applied successfully
- Multiple anomaly clusters identified with varying severity
- Multi-flagged records present (records caught by 3+ techniques)
- Distributions computed for numeric fields
- Entity concentrations identified
- Cached survey loads instantly on subsequent runs

**Failure modes:**
- Data source returns no records -- survey returns `{"error": "No records provided"}`
- All columns are text (no numeric) -- statistical techniques limited to TF-IDF and keyword analysis
- Very small dataset (<20 records) -- techniques may not find meaningful patterns
- Cache hash collision (extremely rare) -- stale results used

**Key metrics:**
- Number of techniques applied (target: 6+)
- Number of anomaly clusters (typical: 5-15)
- Number of multi-flagged records (typical: 10-50)
- Time to complete (typical: 5-30 seconds depending on record count)

### 7.2 Step 2: Briefing (Common Knowledge Baseline)

**What happens mechanically:**
The system checks for a cached briefing file in `catalog/briefings/`. If found, it loads without LLM cost. If not found (and using v2 prompts), it generates one from catalog records and survey results.

**LLM prompt used:** Briefing generation prompt (in `briefer.py`)

**Inputs:**
- Catalog records (bulk metadata)
- Survey results from AnalyticalSurvey
- Data source name

**Outputs:**
- `common_knowledge`: Text describing what domain practitioners already know
- Cached to `catalog/briefings/briefing_{source}.md`

**Working correctly looks like:**
- Briefing covers the major known patterns in the domain
- Specific enough that workers can recognize when their finding matches a known shape
- Not so specific that it constrains investigation
- Loads from cache on subsequent runs (cost: $0)

**Failure modes:**
- No bulk records available -- briefing generation skipped
- Briefing is too vague -- workers cannot calibrate novelty
- Briefing is too specific -- workers suppress genuinely novel findings

**Key metrics:**
- Briefing length (typical: 500-2000 characters)
- Cost (first run: ~$0.02-0.05; subsequent: $0)

### 7.3 Step 3: Genesis (Charter Generation)

**What happens mechanically:**
Genesis takes a sample of catalog records (up to 200), survey findings summary, and briefing text. It passes these to the LLM with `CHARTER_PROMPT`, which produces the organizational charter in CEO directive voice.

**LLM prompt used:** `CHARTER_PROMPT`

**Inputs:**
- `corpus_metadata`: Sample of records (lightweight -- large fields like descriptions truncated)
- `survey_findings`: Summary of AnalyticalSurvey results (record count, techniques, outlier count, top clusters)
- `briefing`: Common knowledge baseline text
- `budget`: Total investigation budget

**Outputs:**
- `charter`: The organizational charter text (under 800 words)
- `corpus_summary`: Brief description of what was generated
- `lenses`: Empty list (lenses are replaced by the charter in the role-authoring path)
- `cost`: LLM cost for this step

**Working correctly looks like:**
- Charter is written in directive voice, not report voice
- Covers all four required sections (what, known, standards, stakes)
- "What is already known" section structures knowledge as categories with examples
- Quality bar is concrete with specific examples of impressive vs. obvious findings
- Under 800 words

**Failure modes:**
- Empty charter -- orchestrator aborts the run
- Charter is generic (doesn't reference specific survey findings) -- workers lack calibration
- Charter is too long -- workers skip reading it
- Charter prescribes investigation areas (it should not -- that's the operational plan's job)

**Key metrics:**
- Charter word count (target: 400-800)
- Cost (typical: $0.02-0.04)
- Whether charter references specific survey findings

### 7.4 Step 4: Workspace Setup

**What happens mechanically:**
The orchestrator creates a workspace directory at `output/{run_id}/workspace/` and writes `charter.md`. Rules of engagement (`rules.md`) are written if an operational plan generates them.

**LLM prompt used:** `OPERATIONAL_PLAN_PROMPT` (for rules generation)

**Inputs:**
- Charter text from genesis
- Corpus shape information
- Total budget

**Outputs:**
- `workspace/charter.md`: The organizational charter
- `workspace/rules.md`: Rules of engagement for all workers
- Initial scopes with scope level classifications, budgets, and success criteria

**Working correctly looks like:**
- Both files exist and contain substantial content
- Rules are operational (specific enough that a depth-5 worker knows how to behave)
- Budget allocations sum to total budget
- Each initial scope has a scope level (manager/worker/ambiguous) with justification

**Failure modes:**
- Workspace directory creation fails (permissions)
- Rules are aspirational instead of operational
- Budget allocations don't sum correctly

**Key metrics:**
- Rules word count
- Number of initial scopes
- Budget allocation accuracy (should sum to total)

### 7.5 Step 5: Exploration

This is the main phase where all discovery happens. It operates as a recursive tree of `RoleWorkerNode` instances.

**What happens mechanically:**

1. **Engagement lead spawns** with 85% of total budget, charter as scope, system-authored role
2. **Formation assessment** -- engagement lead decides to hire (charter scope is too broad)
3. **Role authoring** -- engagement lead designs 3-5 hire roles with different data filters
4. **Parallel execution** -- all hires run concurrently (semaphore limits to 3 simultaneous LLM calls)
5. **Each hire** follows the node lifecycle: formation assessment -> investigate or hire -> reassessment -> resolve/extend/hire
6. **Turn 2 review** -- managers evaluate hires, spawn continuations if warranted
7. **Recursive** -- hires may themselves become managers, going deeper

**LLM prompts used:**
- `NODE_REASONING_PROMPT_V2`: Main formation + investigation prompt
- `NODE_FORCE_RESOLVE_OVERRIDE_V2`: Chain circuit breaker
- `WORKER_REASSESSMENT_PROMPT_V2`: Mid-investigation reassessment
- `WORKER_EXTENSION_PROMPT_V2`: Extended investigation (second reasoning turn)
- `MANAGER_TURN2_PROMPT_V2`: Manager evaluation of hires

**Inputs per node:**
- Role definition (name, mission, bar, heuristic)
- Scope description and purpose
- Parent context (evidence motivating this hire)
- Workspace context (charter + rules)
- Data source filter schema
- Budget information (allocated, parent pool, phase remaining)
- Fetched data (up to 100 records via assigned filter)

**Outputs per node:**
- Formation assessment (investigate vs. hire, with reasoning trace)
- Observations (evidence packets) if investigating
- Hire directives (role definitions + data assignments) if hiring
- Self-evaluation (purpose addressed, evidence quality, follow-up threads, capability gaps, adjacent findings)
- Synthesis role (if hiring -- defines cross-referencing standards)

**Working correctly looks like:**
- Tree depth of 3-5 for $5-$10 runs
- Branching factor of 2-4 at each level
- Most leaf workers produce 3-8 observations each
- Zero-observation nodes < 10% of total
- Each hire examines different data (verified by different `data_filter` values)
- Turn 2 spawns 1-3 continuations when budget and threads warrant it
- Budget utilization > 75% of exploration cap

**Failure modes:**
- All hires get the same data filter -- convergent findings, wasted budget
- Manager doesn't author concrete bars -- Turn 2 evaluation is meaningless
- Excessive decomposition (depth 6+ with few observations) -- budget spent on overhead
- Workers resolve immediately without reassessment -- shallow observations
- Chain circuit breaker fires frequently -- indicates decomposition without value
- Budget exhaustion before leaf nodes get their turn -- poor allocation by managers

**Key metrics:**
- Nodes spawned vs. planned
- Observations per node (target: 3-8 for leaf workers)
- Zero-observation node percentage (target: < 10%)
- Max depth reached
- Average branching factor
- Budget utilization (exploration spent / exploration cap)
- Turn 2 continuation rate
- Reassessment outcome distribution (resolve / extend / hire)

### 7.6 Step 6: Synthesis

**What happens mechanically:**
After exploration completes, the synthesizer cross-references all observations from all nodes. It looks for three types of emergent patterns:

1. **Reinforced patterns:** Two observations that independently support the same conclusion
2. **Contradictions:** Two observations from different sources that conflict
3. **Cross-cutting patterns:** Patterns visible only from combining observations that no single investigator could have seen

If a synthesis role was authored by the engagement lead, the synthesizer uses that role's bar and heuristic to filter what cross-cutting patterns are worth surfacing.

**LLM prompt used:** `SYNTHESIS_PROMPT_V2` (with authored role) or `SYNTHESIS_PROMPT` / `SYNTHESIS_LIGHT_PROMPT` (without)

**Inputs:**
- All observations from all child nodes (formatted as investigator reports)
- Synthesis role (name, bar, heuristic) if authored
- Workspace context (charter)

**Outputs:**
- `reinforced`: Patterns supported by multiple independent observations
- `contradictions`: Conflicting observations from different sources (with side_a and side_b including specific data points)
- `cross_cutting_patterns`: Novel patterns emerging from combination (with evidence chain and inferred links)
- `discovered_questions`: Questions that emerged from the investigation
- `unresolved_threads`: Things needing more investigation

**Working correctly looks like:**
- Each reinforced pattern cites 2+ specific observations with source identifiers
- Each contradiction cites specific data points from both sides
- Cross-cutting patterns include evidence chains with source tracing
- Inferred links are explicitly flagged as inference, not data
- Discovered questions are specific and answerable

**Failure modes:**
- Synthesis hallucinates connections not supported by observations
- Citations are vague (topic names instead of doc_ids)
- All findings are reinforced patterns (no contradictions or cross-cutting patterns)
- Parse error on synthesis output -- fallback to empty synthesis

**Key metrics:**
- Number of reinforced patterns
- Number of contradictions found
- Number of cross-cutting patterns
- Number of discovered questions
- Cost (typical: $0.04-0.10)

### 7.7 Step 7: Deep Dives

**What happens mechanically:**
The orchestrator asks the LLM to select 2-3 findings from synthesis that would benefit most from deeper investigation. For each target, it spawns a `RoleWorkerNode` with a deep-dive role designed for targeted follow-up.

**LLM prompt used:** `DEEP_DIVE_SELECTION_PROMPT` (for selecting targets), then `NODE_REASONING_PROMPT_V2` (for each deep-dive node)

**Inputs:**
- All synthesis findings (reinforced, contradictions, cross-cutting)
- Top 15 observations from exploration
- Available deep-dive budget

**Outputs per deep-dive:**
- Additional observations that quantify, trace, or verify the selected finding
- Evidence that strengthens or refutes the synthesis finding

**Working correctly looks like:**
- 2-3 deep-dive targets selected (not more -- budget is limited)
- Each deep-dive produces specific additional evidence
- Deep-dive observations reference and extend synthesis findings
- Budget stays within deep-dive allocation

**Failure modes:**
- Selection prompt fails to parse -- deep dives skipped
- Deep dives duplicate synthesis findings without adding new evidence
- Budget exhausted before deep dives run
- Deep dives select low-value targets

**Key metrics:**
- Number of deep dives executed
- Observations per deep dive
- Cost per deep dive
- Whether deep-dive observations add to synthesis findings

### 7.8 Step 8: Validation

**What happens mechanically:**
Each Tier 3-5 finding (contradictions and cross-cutting patterns from synthesis) goes through skeptical review. The validator separates factual claims from interpretive claims and evaluates them independently.

**LLM prompt used:** `VALIDATION_PROMPT`

**Inputs per finding:**
- Finding type (contradiction or cross_cutting_pattern)
- Finding description
- Evidence chain (both sides for contradictions, full chain for cross-cutting)

**Outputs per finding:**
- `factual_assessment`: Verdict (CONFIRMED / REFUTED / MISSING_EVIDENCE) with verifiable claims listed
- `interpretive_assessment`: Confidence (WELL_SUPPORTED / PLAUSIBLE / SPECULATIVE) with interpretive claims listed
- `verdict`: Overall (confirmed / confirmed_with_caveats / weakened / refuted / needs_verification)
- `adjusted_confidence`: Post-validation confidence score (0-1)
- `adjusted_tier`: Tier assignment after validation
- `is_pipeline_issue`: Whether this finding is about data collection rather than the corpus
- `verification_action`: Specific lookup that would resolve remaining uncertainty
- `revised_finding`: Reworded finding separating facts from interpretation

**Working correctly looks like:**
- Each finding gets a clear verdict with reasoning
- Factual and interpretive claims are separated correctly
- Pipeline issues are identified and separated from corpus findings
- Revised findings are more precise than originals
- Confirmation rate of 40-70% (lower suggests poor synthesis; higher suggests insufficient skepticism)

**Failure modes:**
- Validator fails to parse -- finding gets "needs_verification" default
- Validator is too lenient (confirms everything) or too strict (refutes everything)
- Pipeline issues not identified -- data artifacts reported as findings
- Budget exhausted before all findings validated

**Key metrics:**
- Findings validated count
- Confirmation rate (confirmed + confirmed_with_caveats / total)
- Pipeline issues identified
- Cost per validation (typical: $0.02-0.04)

### 7.9 Step 9: Significance Gate

**What happens mechanically:**
Each validated finding (confirmed, confirmed_with_caveats, weakened, or needs_verification) is scored on novelty and actionability. Only findings scoring 3.0+ on the composite proceed to impact analysis.

**LLM prompt used:** `SIGNIFICANCE_PROMPT`

**Inputs per finding:**
- Finding description (revised if available)
- Evidence chain
- Validation status
- Common knowledge briefing (for novelty calibration)

**Outputs per finding:**
- `genuine`: Whether claims trace to specific observed data
- `commonly_known`: Whether briefing already covers this
- `novelty`: Score 1-5 with reasoning
- `actionability`: Score 1-5 with reasoning
- `who_cares`: Specific roles or teams affected
- `what_it_unlocks`: What happens if someone acts on this
- `composite_score`: Average of novelty + actionability
- `tier_assignment`: headline / significant / noted
- `headline`: One sentence that makes someone stop scrolling
- `recommendation`: proceed_to_impact / note_only / discard

**Working correctly looks like:**
- Genuine findings are not marked as not genuine simply because "more investigation is needed"
- Commonly known patterns (covered by briefing) score 1-2 on novelty
- Findings with specific names, numbers, and verifiable claims score 3+ on novelty
- Headlines are rare (0-2 per run) and genuinely surprising
- NOTED findings are correctly identified as low-value

**Failure modes:**
- All findings score the same (poor calibration)
- Commonly known patterns score high on novelty (briefing not used effectively)
- No findings pass the 3.0 threshold (overly strict or poor synthesis)
- Budget exhausted before all findings scored

**Key metrics:**
- Headlines count (typical: 0-3)
- Significant count (typical: 3-8)
- Noted count (typical: 5-15)
- Average composite score
- Cost per significance check (typical: $0.01-0.03)

### 7.10 Step 10: Impact Analysis

**What happens mechanically:**
Findings that scored "headline" or "significant" in the significance gate get full impact analysis. Findings are processed highest-score-first so the best findings get analyzed if budget runs out.

**LLM prompt used:** `IMPACT_PROMPT`

**Inputs per finding:**
- Finding description
- Evidence chain
- Post-validation confidence score

**Outputs per finding:**
- `affected_parties`: Specific groups impacted (not "businesses" but "small businesses with federal contracts under $1M")
- `estimated_scale`: How many people/entities affected (with source or "unknown")
- `financial_exposure`: Quantified if possible, mechanism description if not
- `risk_scenario`: Specific, concrete scenario where this finding causes harm
- `who_needs_to_know`: Specific organizations, offices, or roles
- `urgency`: CRITICAL / HIGH / MEDIUM / LOW
- `actionability`: Specific next step someone could take

**Working correctly looks like:**
- Affected parties are specific (named roles, specific organization types)
- Scale estimates cite publicly known figures when available
- Risk scenarios are concrete and vivid, not abstract
- Urgency ratings are calibrated (CRITICAL is rare, MEDIUM is common)
- Actionability describes a specific next step, not a vague recommendation

**Failure modes:**
- Impact analysis is generic ("many organizations could be affected")
- Financial exposure is speculative without basis
- Urgency is inflated
- Parse error -- fallback impact with "unknown" fields

**Key metrics:**
- Impact analyses completed
- Urgency distribution (typical: 0-1 critical, 2-4 high, remainder medium/low)
- Cost per impact analysis (typical: $0.02-0.04)

### 7.11 Step 11: Report Generation

**What happens mechanically:**
All exploration data is compiled and passed to the LLM, which generates a structured five-tier markdown report.

**LLM prompt used:** `REPORT_PROMPT`

**Inputs:**
- Exploration metadata (nodes, observations, depth, time, cost breakdown)
- Corpus summary from genesis
- Lenses used
- All synthesis results
- All observations (grouped by type)
- Validated findings
- Impact analyses
- Unresolved threads

**Outputs:**
- Markdown report with required structure:
  - Header with metadata
  - Corpus Structure Map
  - Tier 1: Common Knowledge (confirmed from data)
  - Tier 2: Structural Insights (organization vs. appearance)
  - Tier 3: Contradictions (with citations, validation, impact)
  - Tier 4: Gaps (same format)
  - Tier 5: Cross-Cutting Patterns (same format)
  - Discovered Questions
  - Unresolved Threads
  - Evidence Chains
  - Exploration Statistics

**Working correctly looks like:**
- All five tiers present with appropriate content
- Tier 1 findings build credibility (basic facts confirmed from data)
- Tier 3-5 findings include validation verdicts and impact assessments
- Every finding in Tiers 3-5 has citations traceable to specific documents
- Clear distinction between "observed in data" and "inferred from observations"
- Pipeline issues separated into their own section (if any)

**Failure modes:**
- Report is too long (LLM hits output token limit)
- Findings lack citations
- Tiers are empty (poor exploration or synthesis)
- Inference presented as fact

**Key metrics:**
- Report length (typical: 2,000-8,000 words)
- Tier 3-5 finding count
- Cost (typical: $0.05-0.15)

### 7.12 Step 12: Reader Test

**What happens mechanically:**
After the report is generated, the reader test extracts Tier 3-5 findings from the report markdown and scores each against the charter's standards. Up to 10 findings are scored (capped for cost).

**LLM prompt used:** `READER_TEST_PROMPT` (defined inline in `reader_test.py`)

**Inputs per finding:**
- Organizational charter text
- Finding summary and evidence (extracted from report markdown)
- Validation status

**Outputs per finding:**
- `factual_novelty`: yes / marginal / no
- `interpretive_certainty`: high / medium / low
- `combined_score`: yes / yes_factual / marginal / no
- `reasoning`: Overall assessment
- `what_practitioner_knows`: Closest known fact
- `what_is_new`: What this finding adds

**Aggregate outputs:**
- Summary counts: yes, yes_factual, marginal, no
- Total cost

**Working correctly looks like:**
- At least 50% of findings score "yes" or "yes_factual"
- "No" findings correctly identify commonly known patterns
- Reasoning references specific charter standards
- Results correlate with significance scores (headlines should be "yes")

**Failure modes:**
- No findings extracted from report (markdown parsing failure)
- No charter found in workspace
- All findings score "no" (poor investigation quality or overly strict scoring)
- All findings score "yes" (poor calibration or overly lenient scoring)

**Key metrics:**
- YES count / total (target: > 40%)
- MARGINAL count
- NO count
- Total reader test cost (typical: $0.05-0.15)

---

## 8. Glossary of Terms

### Role and Mission Terms

| Term | Definition |
|---|---|
| **Role Definition** | The four-field identity of a node: name, mission, bar, heuristic. Authored by the parent at hire time. Defines what the node IS, not just what it does. |
| **Mission** | What excellent work looks like for this node. The aspiration, not a checkbox. Workers reason against the mission, trying to produce their best work. |
| **Bar** (success_bar) | The minimum acceptable output -- below this is failure. Must be concrete enough to fail against. The floor for quality, not the target. |
| **Heuristic** | Posture for ambiguous moments during investigation. Guidance for when the worker faces a judgment call and has no clear rule to follow. |

### Assessment Terms

| Term | Definition |
|---|---|
| **Formation Assessment** | The initial decision a node makes upon spawning: investigate directly or hire specialists. Based on floor test and ceiling test applied to the node's scope, budget, and role. |
| **Floor Test** | "Is delegation overhead less than doing the work directly?" If yes, investigate. If setting up hires costs more than doing the analysis, don't hire. |
| **Ceiling Test** | "Can I hold this scope at my bar's required depth in one careful pass?" If yes, investigate. If the scope is too large or complex for one pass at the required depth, hire. |
| **Reassessment** | Mid-investigation re-evaluation of the floor and ceiling tests using information gained from actual investigation. Can change a worker's trajectory from resolve to extend or hire. |

### Evaluation Terms

| Term | Definition |
|---|---|
| **MET** | Bar compliance classification: each bar element has a corresponding output element that clears it. The hire did what was asked. |
| **POOR_REASONING** | Bar compliance classification: specific bar elements are unmet, and the hire had the data to meet them. The hire had the means but failed to deliver. |
| **WRONG_ROLE** | Bar compliance classification: the hire produced reasonable work that doesn't map to the bar. The bar was misaligned with the territory. A failure of the manager's role authoring, not the hire's work. |
| **COMMITTED** | Commitment classification: the hire used its budget meaningfully. Any remaining unused budget could not support meaningful additional work. |
| **UNDERFIRED** | Commitment classification: the hire has substantial unused budget AND identifiable threads AND resolved anyway. The hire could have done more and didn't. Requires specific evidence: unused budget amount, specific threads, why they'd be substantive. |
| **OVERFIRED** | Commitment classification: continued past diminishing returns. Budget consumed on redundant work. The hire should have stopped sooner. |

### Evidence Terms

| Term | Definition |
|---|---|
| **Observation** | An evidence packet produced by a node. Contains raw evidence (actual data values), statistical grounding, local hypothesis, source citation, confidence, and signal strength. |
| **Evidence Packet** | Synonym for observation. Structured data, not prose. Every packet must contain specific data points, not summaries. |
| **Signal Strength** | Classification of an observation's novelty: `data_originated_novel` (required data AND not in briefing), `data_originated_confirmatory` (required data BUT restates briefing), `confirmatory` (expected without reading data). |
| **Raw Evidence** | The specific data from records -- actual values, numbers, text from the source. NOT descriptions, summaries, or paraphrases. The test: could someone verify this by looking at the cited source? |
| **Surface and Commit** | Quality gate before emitting observations. SURFACE: name every tension noticed about this observation. COMMIT: for each tension, bind to an action (drop, revise, or keep with justification). |

### Continuation Terms

| Term | Definition |
|---|---|
| **Continuation** | A new node spawned by a Turn 2 manager to continue investigation on a specific thread. Picks up where a previous hire left off, receiving that hire's observations as context. |
| **Continuation Directive** | Instructions for a continuation node, including a new role definition, the thread to pursue, and parent context from the original hire's output. |

### Organizational Terms

| Term | Definition |
|---|---|
| **Charter** | The organizational directive written in CEO voice that sets purpose, standards, and stakes for the entire investigation. Read by every worker. Under 800 words. |
| **Workspace** | Directory containing `charter.md` and `rules.md`. Shared context for all workers in the exploration tree. Located at `output/{run_id}/workspace/`. |
| **Engagement Lead** | The first node in the exploration tree. System-authored role. Receives the charter as scope and 85% of total budget. Expected to hire, not investigate directly. |
| **Rules of Engagement** | Operational policies derived from the charter. Cover budget policy, evidence standards, depth policy, quality bar, and novelty requirements. Written for a depth-5 worker to understand exactly how to behave. |

### Budget Terms

| Term | Definition |
|---|---|
| **Budget Pool** | Shared atomic budget tracker (`BudgetPool` in `schemas.py`). Prevents parallel overspend. Tracks total, spent, reserved, and per-phase spending. |
| **Phase Limit** | Hard or soft cap on how much a pipeline phase can spend. Exploration (85%) and review (15%) are hard caps. Others are soft limits. |
| **Envelope** | A node's total budget allocation from its parent. The node cannot spend more than its envelope. Surplus (unspent envelope) flows back to the parent pool. |
| **Envelope Floor** | $0.12 -- the minimum viable envelope. Nodes receiving less are rejected at spawn time. Roughly one LLM call with reduced thinking at current Sonnet pricing. |
| **Downstream Reservation** | Budget estimated for phases after exploration (synthesis, validation, impact, report). These are CEILINGS, not reservations -- unused budget flows back to the pool. |

### Quality Terms

| Term | Definition |
|---|---|
| **Reader Test** | Post-report scoring of each finding against the charter's standards. Dual scoring: factual novelty (yes/marginal/no) and interpretive certainty (high/medium/low). |
| **Factual Novelty** | Reader test dimension: would a knowledgeable practitioner say "I didn't know that fact"? The underlying observation, not the interpretation. |
| **Interpretive Certainty** | Reader test dimension: how strongly does the interpretation follow from the cited facts? HIGH = few alternatives, MEDIUM = plausible but other explanations exist, LOW = speculative. |

### Data Terms

| Term | Definition |
|---|---|
| **Data Partitioning** | Assigning different data filters to different hires so each examines different records. Critical for producing diverse findings rather than convergent analysis. |
| **Data Filter** | A dictionary of query parameters sent to the data source API. Must use only parameter names from the filter schema. Different hires get different filter values. |
| **Filter Schema** | Description of what filter parameters a data source accepts. Includes parameter name, type, description, and example value. Provided by `data_source.filter_schema()`. |

### Synthesis Terms

| Term | Definition |
|---|---|
| **Synthesis Role** | A role definition authored by the manager for cross-referencing its hires' findings. Defines what good synthesis looks like for this specific engagement. Same structure: name, bar, heuristic. |
| **Cross-Cutting Pattern** | A pattern visible only from combining observations that no single investigator could have seen. The most valuable synthesis output. |

### Validation Terms

| Term | Definition |
|---|---|
| **Confirmed** | Validation verdict: factual claims confirmed AND interpretive claims well-supported. The finding is solid. |
| **Confirmed with Caveats** | Validation verdict: factual claims confirmed BUT interpretive claims only plausible or speculative. The data is solid; the interpretation is uncertain but reasonable. NOT a weak finding. |
| **Weakened** | Validation verdict: factual claims have missing evidence in part, but partial support exists. |
| **Refuted** | Validation verdict: factual claims refuted or evidence directly contradicts the finding. |
| **Needs Verification** | Validation verdict: factual claims themselves cannot be assessed from available data. Reserved for genuinely unverifiable claims. |
| **Pipeline Issue** | A finding that describes properties of the data collection/extraction process rather than the corpus itself. Separated into a distinct report section. |

### Significance Terms

| Term | Definition |
|---|---|
| **HEADLINE** | Significance tier assignment for composite score 4.0+. Top of report, full impact analysis. Rare (0-2 per run). |
| **SIGNIFICANT** | Significance tier assignment for composite score 3.0-3.9. Prominent in report, gets impact analysis. |
| **NOTED** | Significance tier assignment for composite score below 3.0. Listed briefly, no impact analysis. |

---

## 9. Metrics Reference

### 9.1 Run-Level Metrics

| Metric | What It Measures | Good | Bad |
|---|---|---|---|
| **Total cost vs. budget** | Budget utilization. Spent / total. | 75-95% utilized | < 50% (wasted budget) or > 100% (impossible, but near-100% with poor quality suggests rushing) |
| **Nodes spawned** | Tree size. Total nodes created. | Proportional to budget (~20 nodes per dollar) | Far below budget capacity (under-exploration) or many zero-observation nodes (over-decomposition) |
| **Nodes resolved** | Nodes that produced observations. | > 90% of spawned | < 70% (many nodes failed or were budget-rejected) |
| **Observations collected** | Total evidence packets produced. | 3-8 per leaf node, 50-200+ total | < 20 total (shallow investigation) or many low-signal observations |
| **Max depth reached** | Deepest level in the tree. | 3-5 for $5-$10 runs, 4-6 for $50+ | 1-2 (no decomposition, budget not used) or 7+ (excessive decomposition) |
| **Branching factor** | Average children per non-leaf node. | 2-4 | 1 (chain, no real decomposition) or 6+ (too many underfunded children) |
| **Zero-observation nodes** | Nodes that produced no findings. | < 10% of total | > 25% (investigation failures or poor scoping) |
| **Duration** | Wall-clock time. | 3-10 min for $5, 10-30 min for $10, 30-120 min for $50 | Much longer suggests rate limiting or excessive sequential chains |

### 9.2 Quality Metrics

| Metric | What It Measures | Good | Bad |
|---|---|---|---|
| **Findings confirmed** | Validation confirmation rate. Confirmed + confirmed_with_caveats / total validated. | 40-70% | < 20% (synthesis producing weak findings) or > 90% (validator too lenient) |
| **Findings refuted** | Findings that failed validation. | < 20% | > 40% (synthesis producing invalid findings) |
| **Pipeline issues identified** | Data artifacts separated from corpus findings. | Appropriate count (0-3 is normal) | Many pipeline issues mixed into corpus findings |
| **Reader test: YES count** | Findings that pass the novelty bar. | > 40% of scored findings | < 20% (poor novelty calibration or generic findings) |
| **Reader test: NO count** | Findings that fail the novelty bar. | < 30% of scored findings | > 60% (investigation producing commonly known results) |
| **Significance: HEADLINE count** | Truly surprising, actionable findings. | 0-3 per run (headlines are rare) | > 5 (significance scoring too lenient) or 0 across multiple runs (never finding anything novel) |
| **Significance: NOTED count** | Low-novelty or low-actionability findings. | Expected to be the majority | 0 (all findings are significant -- unlikely and suspicious) |
| **Cost per validated finding** | Efficiency of the investigation. Total cost / confirmed findings. | $0.50-$2.00 per confirmed finding | > $5.00 (very inefficient) |

### 9.3 Node-Level Metrics

| Metric | What It Measures | Good | Bad |
|---|---|---|---|
| **Budget utilization (per node)** | Spent / allocated for each node. | 60-90% | < 30% (scope didn't warrant budget) or > 100% (overspend, shouldn't happen) |
| **Observations per node** | Evidence production rate. | 3-8 for leaf workers, 0 for pure managers | 0 for workers (investigation failure) or 20+ (quantity over quality) |
| **Bar compliance rate** | Percentage of hires meeting their bar. | > 70% MET | > 30% POOR_REASONING (poor role authoring) or > 20% WRONG_ROLE (manager misread the territory) |
| **Commitment distribution** | COMMITTED / UNDERFIRED / OVERFIRED across all hires. | > 60% COMMITTED | > 30% UNDERFIRED (nodes not pushing hard enough) or > 20% OVERFIRED (budget wasted on diminishing returns) |
| **Reassessment outcome distribution** | RESOLVE / INVESTIGATE_FURTHER / HIRE across all reassessments. | Majority RESOLVE with meaningful INVESTIGATE_FURTHER minority | All RESOLVE (no reassessment value) or all INVESTIGATE_FURTHER (infinite extension) |
| **Extension observation gain** | Additional observations from INVESTIGATE_FURTHER extensions. | 2-5 additional observations per extension | 0 (extension produced nothing new) or restated initial observations |

---

## 10. Output Structure

Every run produces a directory at `output/{run_id}/` containing:

### 10.1 Core Output Files

| File | Format | Contents |
|---|---|---|
| `report.md` | Markdown | Five-tier exploration report with all findings, citations, validation verdicts, and impact assessments. The primary human-readable output. |
| `metrics.json` | JSON | Complete run metrics including cost breakdown, quality metrics, token usage, coverage stats, and reader test results (if run). |
| `tree.json` | JSON | Complete exploration tree structure showing parent-child relationships, observations, costs, and thinking logs for every node. |
| `events.jsonl` | JSONL | Chronological event stream for playback. Every system event (node spawned, node resolved, thinking chunk, budget update, finding discovered, etc.) with timestamps. |

### 10.2 Workspace Directory

| File | Format | Contents |
|---|---|---|
| `workspace/charter.md` | Markdown | The organizational charter generated by genesis. Read by every worker. |
| `workspace/rules.md` | Markdown | Rules of engagement derived from the charter. Operational policies for all workers. |

### 10.3 Per-Node Output

| Directory/File | Format | Contents |
|---|---|---|
| `nodes/{node_id}.json` | JSON | Per-node output including: node_id, parent_id, scope, tree_position, role name and bar, observations (full evidence packets), child count, thinking log (per-turn structure), Turn 2 result, self-evaluation metrics, token usage, cost. |
| `diagnostics/{node_id}.json` | JSON | Per-node diagnostic data including: role, scope, purpose, data received (record count), anomaly targets, thinking summary, output stats (observation count, children spawned, evidence cited), self-evaluation, budget (envelope, spent, surplus, depth), decision type, Turn 2 result, reassessment turns. |

### 10.4 Additional Output

| File | Format | Contents |
|---|---|---|
| `transcripts/` | Directory | Human-readable transcripts of each node's reasoning, generated by `build_transcripts.py`. Optional -- generated if the script is available. |
| `dashboard.md` | Markdown | Summary dashboard of the run (if generated). |
| `knowledge_graph.json` | JSON | Export of the SQLite knowledge graph: entities, relationships, contradictions, observations. Persists across runs. |
| `full_diagnostic.txt` | Text | Complete diagnostic dump of every node's input/output in a single file. Useful for debugging. |
| `comparison_prompt.md` | Markdown | A prompt you can give to other AI systems (Gemini Deep Research, etc.) to test reproducibility of findings. |

### 10.5 Catalog Directory (Shared Across Runs)

| File | Format | Contents |
|---|---|---|
| `catalog/survey_cache_{hash}.json` | JSON | Cached AnalyticalSurvey results. Keyed by hash of record count + boundary records. Loaded on subsequent runs if data hasn't changed. |
| `catalog/{source}_enriched.jsonl` | JSONL | Cached enriched records from the data source. Prevents re-fetching data that has already been downloaded. |
| `catalog/briefings/briefing_{source}.md` | Markdown | Cached common knowledge briefing for each data source. |
| `catalog/run_history.jsonl` | JSONL | Cumulative run-over-run comparison data. |

---

## 11. How to Run

### 11.1 Prerequisites

```bash
# Python 3.14+ required
python3 --version

# Set API key
export ANTHROPIC_API_KEY=your_key_here

# Install dependencies
pip install anthropic httpx websockets scikit-learn pandas numpy python-dotenv
```

### 11.2 CLI Usage

```bash
# Basic run with specified source and budget
python3 run.py --source npm --budget 10

# With live visualizer in browser
python3 run.py --source sec --budget 10 --visualize

# Using v2 prompts (role-authoring path)
python3 run.py --source npm --budget 10 --prompts v2

# With user hints
python3 run.py --source npm --budget 10 --hint "security focus" --hint "supply chain"

# Estimation mode (survey first, then choose tier)
python3 run.py --source npm --estimate

# Auto-proceed with a tier
python3 run.py --source npm --estimate --auto-proceed balanced

# Replay a previous run in the visualizer
python3 run.py --playback output/{run_id}/events.jsonl --speed 10

# Query the knowledge graph from previous runs
python3 run.py --query "single maintainer packages"

# Interactive mode (browser-based source selection)
python3 run.py --visualize
```

### 11.3 CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--source` | string | None | Data source: `npm`, `sec` or `sec_edgar`, `federal_register`. Omit with `--visualize` for browser selection. |
| `--budget` | float | None | Maximum cost in dollars. If not specified: $20 for headless, interactive selection for `--visualize`. |
| `--hint` | string (repeatable) | [] | Optional context hints. Influence lens weighting, not lens selection. |
| `--estimate` | flag | false | Survey corpus and show cost estimates before exploring. |
| `--auto-proceed` | choice | None | Auto-select tier: `thorough`, `balanced`, `focused`, `scout`. |
| `--output-dir` | string | `output` | Output directory path. |
| `--visualize` | flag | false | Launch real-time D3.js visualization in browser. |
| `--query` | string | None | Query the knowledge graph from previous runs. |
| `--playback` | string | None | Path to `events.jsonl` file for replay. |
| `--speed` | int | 10 | Playback speed multiplier. |
| `--prompts` | choice | `v1` | Prompt version: `v1` (legacy) or `v2` (role-authoring path). |
| `--partition-gate` | choice | `on` | MECE partition gate: `on` (halt on failure) or `off` (instrumentation only). |

### 11.4 Available Data Sources

| Source ID | Data Source | Auth | Cache |
|---|---|---|---|
| `npm` | npm package registry | None (public API) | `catalog/npm_enriched.jsonl` |
| `sec` or `sec_edgar` | SEC EDGAR financial filings | User-Agent header | `catalog/sec_enriched.jsonl` |
| `federal_register` | US Federal Register | None (public API) | Per-request |

### 11.5 Budget Recommendations

| Budget | Use Case | Expected Output |
|---|---|---|
| **$5** | Quick scout. Surface-level anomalies. | ~30 nodes, ~60 observations, depth 3-4. Good for verifying the system works on a new data source. |
| **$10** | Standard exploration. Most patterns found. | ~45 nodes, ~180 observations, depth 4+. Good balance of breadth and depth. |
| **$15-$25** | Thorough exploration. Multiple deep dives. | ~100 nodes, ~300+ observations, depth 4-5. Cross-cutting patterns emerge reliably. |
| **$50** | Deep investigation. Full coverage. | ~200+ nodes, ~500+ observations, depth 5-6. Comprehensive findings with strong validation. |
| **$200** | Exhaustive analysis. | ~1000+ nodes. Maximum coverage and cross-referencing. |

### 11.6 Visualizer Usage

When running with `--visualize`, a D3.js real-time visualization opens in your browser showing:

- Live exploration tree growing as nodes spawn and resolve
- Node reasoning displayed as thinking chunks stream in
- Budget utilization bar
- Phase transitions
- Finding discoveries highlighted
- Validation and impact results

**Playback mode:** Replay any previous run:

```bash
python3 run.py --playback output/{run_id}/events.jsonl --speed 10
```

Speed multiplier controls how fast events replay. `--speed 1` is real-time, `--speed 10` is 10x faster.

---

## 12. Data Source Connector Pattern

### 12.1 The Interface

Every data source implements the `DataSource` base class (defined in `mycelium/data_sources/base.py`):

```python
class DataSource:
    """Base class for all data sources."""

    async def survey(self, filters: dict) -> dict:
        """Return ecosystem shape and metadata.
        
        Called by genesis to understand what's in the data source.
        Returns a dict with structural metadata: record count, categories,
        time ranges, entity types, etc.
        """
        raise NotImplementedError

    async def fetch(self, filters: dict, max_results: int) -> list[dict]:
        """Return records matching the given filters.
        
        Called by every exploration node to get data for analysis.
        Filters come from the filter schema -- only parameters defined
        in filter_schema() are valid.
        
        Returns a list of dicts, each representing one record.
        """
        raise NotImplementedError

    async def fetch_bulk_metadata(self, max_records: int,
                                   progress_callback=None) -> list[dict]:
        """Return all accessible records for AnalyticalSurvey.
        
        Called once at the start of a run to populate the catalog.
        Should return lightweight metadata (not full content) for
        as many records as possible, up to max_records.
        
        progress_callback receives {"fetched": N, "total_estimated": M}
        """
        raise NotImplementedError

    def filter_schema(self) -> dict:
        """Describe what filter parameters this data source accepts.
        
        Returns a structured contract so the LLM knows what queries work.
        Each parameter: type, description, example, required flag.
        
        Example:
        {
            "keyword": {
                "type": "string",
                "description": "Search term matched against record titles",
                "example": "machine learning",
                "required": false
            },
            "date_range": {
                "type": "string",
                "description": "Date range in YYYY-MM-DD format",
                "example": "2024-01-01,2025-01-01",
                "required": false
            }
        }
        """
        return {}

    async def close(self):
        """Clean up connections, close HTTP clients."""
        pass
```

### 12.2 How to Add a New Data Source

1. **Create a new file** in `mycelium/data_sources/`:

```python
# mycelium/data_sources/my_source.py
from .base import DataSource
import httpx

class MySource(DataSource):
    source_name = "My Data Source"
    
    def __init__(self):
        self._client = httpx.AsyncClient()
    
    async def survey(self, filters: dict) -> dict:
        # Return structural metadata about the data source
        return {
            "total_records": 50000,
            "categories": ["cat_a", "cat_b", "cat_c"],
            "date_range": "2020-01 to 2026-04",
            "description": "My data source covers..."
        }
    
    async def fetch(self, filters: dict, max_results: int) -> list[dict]:
        # Query the API with the given filters
        # Return a list of record dicts
        keyword = filters.get("keyword", "")
        response = await self._client.get(
            "https://api.example.com/search",
            params={"q": keyword, "limit": max_results}
        )
        return response.json().get("results", [])
    
    async def fetch_bulk_metadata(self, max_records: int,
                                   progress_callback=None) -> list[dict]:
        # Fetch all records for the analytical survey
        # Use pagination, report progress
        records = []
        for page in range(max_records // 100):
            batch = await self._fetch_page(page)
            records.extend(batch)
            if progress_callback:
                progress_callback({
                    "fetched": len(records),
                    "total_estimated": max_records
                })
        return records
    
    def filter_schema(self) -> dict:
        return {
            "keyword": {
                "type": "string",
                "description": "Search term for record titles and descriptions",
                "example": "climate change",
                "required": False
            },
            "category": {
                "type": "string",
                "description": "Filter by record category",
                "example": "cat_a",
                "required": False
            }
        }
    
    async def close(self):
        await self._client.aclose()
```

2. **Register in `run.py`:**

```python
from mycelium.data_sources.my_source import MySource

def create_data_source(name):
    if name == "my_source":
        return MySource()
    # ... existing sources
```

3. **Test:** Run with `python3 run.py --source my_source --budget 5`

### 12.3 Key Design Principles for Connectors

- **Domain logic stays in the connector.** The connector knows how to query its API, how to paginate, how to cache. Nothing outside the connector knows about the data source's specifics.
- **Records are plain dicts.** The connector returns `list[dict]`. The survey engine, exploration nodes, and synthesis all work on generic dicts.
- **Filter schema is the contract.** The LLM uses the filter schema to construct queries. If a parameter isn't in the schema, nodes can't use it.
- **Cache aggressively.** Enrichment data should be cached to `catalog/` to avoid re-fetching. Both npm and SEC EDGAR connectors cache to JSONL files.
- **Progress callbacks for bulk fetches.** The catalog phase can take minutes for large datasets. Progress callbacks keep the user informed and feed the visualizer.

### 12.4 Current Connectors

**npm Registry** (`mycelium/data_sources/npm_registry.py`):
- Public API, no authentication required
- Filter schema: `keyword` (npm search), `packages` (specific package names), `scope` (npm scope like @babel)
- Caches enriched metadata to `catalog/npm_enriched.jsonl`
- Fetches package details (maintainers, dependencies, download counts, versions)

**SEC EDGAR** (`mycelium/data_sources/sec_edgar.py`):
- Public API, requires User-Agent header
- Filter schema: `keyword` (company name substring), `cik` (CIK number), `sic_code` (industry code)
- Caches enriched filings to `catalog/sec_enriched.jsonl`
- Fetches 10-K filing details (risk factors, financial data, company info)

**Federal Register** (`mycelium/data_sources/federal_register.py`):
- Public API, no authentication required
- Fetches regulations and proposed rules
- No enrichment cache (data is relatively small)

---

## 13. Technical Reference

### 13.1 Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.14+ | Runtime (use `python3`, not `python`) |
| `anthropic` SDK | Claude API access. Model: `claude-sonnet-4-20250514` |
| `httpx` | Async HTTP client for data source connectors |
| `websockets` | Real-time visualizer WebSocket server |
| `scikit-learn` | Isolation Forest, DBSCAN, StandardScaler for analytical survey |
| `pandas` | Data manipulation for analytical survey |
| `numpy` | Numerical computation for analytical survey |
| `python-dotenv` | Environment variable loading |

No databases. No vector stores. No RAG. Just API calls and reasoning.

### 13.2 Key Source Files

| File | Purpose |
|---|---|
| `run.py` | CLI entry point. Argument parsing, data source creation, orchestrator invocation, report saving. |
| `mycelium/schemas.py` | All data structures: RoleDefinition, Directive, Observation, NodeResult, SynthesisResult, ValidationResult, ImpactResult, BudgetPool, ExplorationStats, Briefing. |
| `mycelium/prompts_v2.py` | All LLM prompts (role-authoring path). Single source of truth. Never inline prompts elsewhere. |
| `mycelium/worker_v2.py` | RoleWorkerNode: the core node implementation. Formation assessment, investigation, hiring, reassessment, extension, Turn 2 evaluation. |
| `mycelium/orchestrator.py` | Pipeline coordinator. Runs genesis, exploration, deep dives, validation, significance, impact. Pure plumbing. |
| `mycelium/survey.py` | AnalyticalSurvey: 8-technique statistical analysis engine. Domain-agnostic. |
| `mycelium/genesis.py` | Charter generation from corpus metadata and survey results. |
| `mycelium/synthesizer.py` | Cross-referencing attention mechanism for finding reinforcements, contradictions, and cross-cutting patterns. |
| `mycelium/validator.py` | Skeptical review: factual vs. interpretive claim separation and verdict. |
| `mycelium/significance.py` | Novelty + actionability scoring. Significance gate. |
| `mycelium/impact.py` | Real-world impact assessment of validated findings. |
| `mycelium/reporter.py` | Five-tier markdown report generation. |
| `mycelium/reader_test.py` | Per-finding scoring against charter standards. |
| `mycelium/events.py` | WebSocket server + events.jsonl recording for visualizer. |
| `mycelium/knowledge_graph.py` | SQLite-backed persistent knowledge graph. |
| `mycelium/workspace.py` | OrgWorkspace: read/write charter.md and rules.md. |
| `mycelium/briefer.py` | Common knowledge briefing generation. |
| `mycelium/data_sources/base.py` | DataSource base class (the connector interface). |
| `visualizer.html` | D3.js real-time tree visualization with reasoning display and playback. |

### 13.3 Concurrency Model

- **Semaphore:** Limits concurrent LLM calls to 3 (`asyncio.Semaphore(3)`)
- **Parallel siblings:** Child nodes run concurrently via `asyncio.gather()`
- **Atomic budget:** `BudgetPool._lock` prevents race conditions in budget accounting
- **Reserve-commit pattern:** Nodes reserve estimated cost before LLM call, commit actual cost after

### 13.4 LLM Call Pattern

Every LLM call in the system follows the same pattern:

```python
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=16000,          # varies by call type
    thinking={"type": "enabled", "budget_tokens": 8000},  # extended thinking
    messages=[{"role": "user", "content": prompt}],
)

# Extract thinking and output
thinking = ""
output = ""
for block in response.content:
    if block.type == "thinking":
        thinking = block.thinking
    elif block.type == "text":
        output = block.text

# Calculate cost
cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000
```

Extended thinking (8000 token budget) is enabled on all exploration node calls. Other calls (genesis, synthesis, validation, significance, impact, report) use standard calls without extended thinking.

### 13.5 Safety Mechanisms

| Mechanism | What It Prevents |
|---|---|
| Chain circuit breaker (`MAX_CHAIN_DEPTH=8`) | Infinite single-child decomposition chains |
| Minimum viable envelope ($0.12) | Spawning nodes with insufficient budget to do useful work |
| Exploration hard cap (85%) | Exploration consuming budget needed for downstream phases |
| Budget pool atomic locking | Parallel nodes overspending shared budget |
| Phase limits | Any single phase consuming disproportionate budget |
| Single-child prohibition | Never spawn exactly one child (resolve or spawn 2+) |
| Max depth limit (6) | Excessive tree depth consuming budget on overhead |

---

## Appendix A: Prompt Reference

All prompts are defined in `mycelium/prompts_v2.py`. This table maps each system action to its prompt:

| Action | Prompt Name | Used By |
|---|---|---|
| Corpus survey + lens generation (legacy) | `GENESIS_PROMPT` | `genesis.py` (v1 path) |
| Organizational charter generation | `CHARTER_PROMPT` | `genesis.py` (v2 path) |
| Operational plan (rules + scopes) | `OPERATIONAL_PLAN_PROMPT` | Orchestrator |
| Budget-aware strategy (legacy) | `PLANNER_PROMPT` | Orchestrator (v1 path) |
| Node reasoning (legacy, 5-step) | `NODE_REASONING_PROMPT` | `node.py` (v1 path) |
| Node reasoning (role-authoring) | `NODE_REASONING_PROMPT_V2` | `worker_v2.py` |
| Chain circuit breaker (legacy) | `NODE_FORCE_RESOLVE_OVERRIDE` | Appended to NODE_REASONING_PROMPT |
| Chain circuit breaker (v2) | `NODE_FORCE_RESOLVE_OVERRIDE_V2` | Appended to NODE_REASONING_PROMPT_V2 |
| Mid-investigation reassessment | `WORKER_REASSESSMENT_PROMPT_V2` | `worker_v2.py` |
| Investigation extension | `WORKER_EXTENSION_PROMPT_V2` | `worker_v2.py` |
| Manager Turn 2 evaluation | `MANAGER_TURN2_PROMPT_V2` | `worker_v2.py` |
| Synthesis (role-anchored) | `SYNTHESIS_PROMPT_V2` | `synthesizer.py` |
| Synthesis (full, no role) | `SYNTHESIS_PROMPT` | `synthesizer.py` |
| Synthesis (light, no role) | `SYNTHESIS_LIGHT_PROMPT` | `synthesizer.py` |
| Deep-dive target selection | `DEEP_DIVE_SELECTION_PROMPT` | Orchestrator |
| Anomaly aggregation | `ANOMALY_AGGREGATION_PROMPT` | Orchestrator |
| Anomaly routing to segments | `ANOMALY_ROUTING_PROMPT` | Orchestrator |
| Skeptical validation | `VALIDATION_PROMPT` | `validator.py` |
| Significance scoring | `SIGNIFICANCE_PROMPT` | `significance.py` |
| Impact analysis | `IMPACT_PROMPT` | `impact.py` |
| Report generation | `REPORT_PROMPT` | `reporter.py` |
| Cost estimation | `ESTIMATE_PROMPT` | `run.py` |
| Tool selection (future) | `EQUIP_PROMPT` | Not currently used |
| Reader test scoring | `READER_TEST_PROMPT` | `reader_test.py` (inline) |

---

## Appendix B: Example Run Output

A typical $10 npm exploration might produce:

```
MYCELIUM -- Engineered Curiosity
Source: NpmRegistrySource
Budget: $10.00

[CATALOG] 1,847 records cataloged -- 12 anomaly clusters, 45 outliers, 23 concentrations
[BRIEFING] Loaded cached briefing (1,234 chars)
[GENESIS] Charter generated (623 words)
[WORKSPACE] Org workspace created

PHASE 1: EXPLORATION
  [1] engagement lead: Hiring 4 workers ($8.50)
  [1.1] supply-chain analyst: RESOLVED: 6 observations
  [1.2] dependency-graph analyst: Hiring 3 workers ($1.80)
  [1.2.1] lodash ecosystem analyst: RESOLVED: 5 observations
  [1.2.2] express middleware analyst: RESOLVED: 4 observations  
  [1.2.3] react toolchain analyst: RESOLVED: 7 observations
  [1.2] REVIEWING hires against authored bars...
    Hire 'lodash ecosystem analyst': MET / COMMITTED
    Hire 'express middleware analyst': MET / UNDERFIRED
    Hire 'react toolchain analyst': MET / COMMITTED
    Turn 2 decision: CONTINUE
  [1.2.C1] express-deep-dive analyst: RESOLVED: 4 observations
  [1.3] ecosystem dynamics analyst: RESOLVED: 8 observations
  [1.4] adoption pattern analyst: RESOLVED: 5 observations
  [1] REVIEWING hires...
    Turn 2 decision: RESOLVE

NODE DIAGNOSTIC SUMMARY (ROLE PATH)
  Total nodes: 10
  Observations: 39
  Max depth: 3

PHASE 2: DEEP DIVES ($0.45 available)
  [DEEP-DIVE 1] Express middleware maintainer concentration: 4 observations
  [DEEP-DIVE 2] TypeScript compiler dependency chain: 3 observations

PHASE 3: VALIDATION (8 findings)
  [VALIDATE 1/8] contradiction: express vs fastify maintainer claims... -> confirmed_with_caveats
  [VALIDATE 2/8] cross_cutting: single-maintainer pattern across frameworks... -> confirmed
  ...

PHASE 5: SIGNIFICANCE GATE (6 findings)
  -> HEADLINE (score: 4.2) -- One person controls 11 of 17 Express middleware packages
  -> SIGNIFICANT (score: 3.5) -- TypeScript compiler has undocumented...
  -> NOTED (score: 2.1) -- npm has many single-maintainer packages
  ...

PHASE 6: IMPACT ANALYSIS (3 findings)
  [IMPACT 1/3] -> Urgency: HIGH
  [IMPACT 2/3] -> Urgency: MEDIUM
  ...

COMPLETE -- 10 nodes, 46 obs, depth 3
Cost: $8.73/$10.00 (87% used)
Reader test: 3 yes, 2 marginal, 1 no ($0.09)
```

---

## Appendix C: Troubleshooting

### Common Issues

| Symptom | Likely Cause | Resolution |
|---|---|---|
| "ANTHROPIC_API_KEY not set" | Environment variable missing | `export ANTHROPIC_API_KEY=your_key` or add to `.env` file |
| Zero observations from all nodes | Data source returning empty results | Check filter schema, verify data source API is accessible |
| All findings score NO on reader test | Investigation producing known patterns | Check if briefing is adequate; increase budget for deeper investigation |
| Budget exhausted in exploration phase | Too many nodes spawned at shallow depth | Reduce branching factor by using higher budget or fewer initial scopes |
| Chain circuit breaker firing frequently | Nodes decomposing without producing observations | Indicates scope descriptions are too vague for direct investigation |
| All hires get WRONG_ROLE | Manager authoring bars misaligned with territory | Charter may be too prescriptive; check workspace/charter.md |
| Visualization not connecting | WebSocket server not starting | Check port 8765 is available; try restarting |
| Survey cache stale | Data source changed but cache hash matches | Delete `catalog/survey_cache_*.json` files |

---

*This document is the definitive reference for Mycelium. If behavior differs from this document, file a bug.*
