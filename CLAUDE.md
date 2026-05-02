# Mycelium — Engineered Curiosity

## Coding Guidelines (Karpathy Principles)

**These four rules override all defaults. Follow them exactly.**

### 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Every changed line should trace directly to the user's request.
- Remove imports/variables that YOUR changes made unused. Don't remove pre-existing dead code.

### 4. Goal-Driven Execution
- Transform tasks into verifiable goals with success criteria.
- For multi-step tasks, state a brief plan with verification steps.
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- Strong success criteria let you loop independently. Weak criteria require clarification — ask.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Architectural Invariants — Do Not Violate

These rules are the architecture, not implementation details. Violating them is not a tradeoff or a shortcut — it produces a different system that no longer matches the PRD.

### 1. The unified node primitive

There is one node class, one node prompt, one code path for formation and investigation. Engagement leads, workers, and managers are descriptive labels for the role a node ended up playing — not structural categories. The same code runs at depth 0, depth 1, and depth 5.

If you find yourself adding a separate prompt constant, a separate class, or a code branch that selects different formation logic based on depth, position, or labels like "is_engagement_lead" — stop. That is the drift this rule exists to prevent.

The one legitimate code branch on node identity is the data-input branch in `worker_v2.py`: a node with no partition (which is true only for the first node, because no parent exists to assign one) receives corpus metadata instead of records. This branch is documented in a comment at the call site. Do not add other branches of this kind.

### 2. Roles are authored by LLM nodes, not by code

A role definition (name, mission, bar, heuristic) describes what kind of cognition this node performs. Every role in the system is authored by another LLM node — its parent. The engagement lead is the only exception: its role is authored by the orchestrator at startup, because no parent exists.

Do not hardcode role definitions in code. Do not select roles from a static list. Do not template roles based on depth, dimension, or any other code-level signal. Roles emerge from the LLM's reasoning about scope and team design. If a role appears in the system without an LLM having authored it, the architecture has drifted.

### 3. Code is plumbing, the LLM is the explorer

This is the existing principle. It is reaffirmed here for emphasis: any architectural decision that moves reasoning out of the LLM and into code violates this principle. The structural input the LLM needs (distributions, cardinality, schema) is computed by code and shown to the LLM. The decision about how to use that input — what to partition on, what break points to choose, what roles to author — is always the LLM's.

### 4. Validation is corpus-grounded, not text-based

The validator runs four parallel operations on each finding, all grounded in the corpus the workers examined: (1) Factual Re-Query fetches actual records to confirm/refute specific claims, (2) Triangulation counts independent observations from different partitions supporting the same pattern, (3) Falsification actively tries to disprove the finding using corpus evidence, (4) Surprise Scoring compares against the common-knowledge briefing. The validator has the same data source access workers have. It does not evaluate findings by reading their text alone — it re-checks claims against the data.

---

## What This Is

An autonomous discovery framework that finds unknown patterns in any information space. Point it at a data source, give it a budget, and it decides what to explore, how deep to go, and what to report. Demonstrated on npm registry (100K+ packages) and SEC EDGAR (41K+ 10-K filings).

## Core Principle: Pure Agentic, Zero Hardcoding

**The code is plumbing. The LLM is the brain.**

NOTHING in the Python code is specific to any data corpus. No npm-specific logic in prompts. No field-name matching in the survey engine. No domain knowledge in the orchestrator. The system must work identically whether pointed at:
- npm registry (software packages)
- SEC EDGAR financial filings
- Hospital patient records (FHIR/HL7)
- Pollution monitoring stations
- A company's Jira tickets

If you find yourself writing `if field == "downloads"` or `if source == "npm"` anywhere except inside a data source connector, you are violating this principle.

**The only things that change between data sources are:**
1. The connector (how to fetch records)
2. The data itself (what fields exist)

**The things that NEVER change:**
- Prompts (they reason about whatever data they receive)
- Survey engine (pure statistics on any list of dicts)
- Exploration logic (recursive decomposition)
- Synthesis, validation, significance, impact pipeline
- Visualizer and event system

## Project Context

- Anurag is a product manager, not an engineer — explain technical decisions in comments, prefer simplicity over cleverness
- This is intended as open source — code quality matters, the codebase IS the demo
- Anyone reading source should understand the architecture in 5 minutes

## Architecture

### Pipeline
Catalog (free) → Genesis → EQUIP → Explore (parallel) → Synthesis → Deep-dive → Validate → Significance → Impact → Report → Metrics

### MECE Partition Gate
At every parent→child boundary, the system verifies child partitions tile the parent's scope:
- **Completeness**: union of child partitions = parent scope (no gaps)
- **Exclusivity**: intersection of any two children = empty (no overlaps)
- **Shape**: every partition translates to a SQL filter (no analytical lenses)

If any check fails and `--partition-gate on`, the run halts with a diagnostic. The gate prevents the pipeline from silently collapsing to sample-based analysis.

### Node Accountability
Every node operates like an employee:
- **Receives PURPOSE** — why it's being asked, not just scope
- **Produces EVIDENCE PACKETS** — structured data (raw_evidence, statistical_grounding, local_hypothesis), not prose
- **Self-evaluates** — before reporting, checks if output addresses purpose
- **Parent reviews** — Turn 2 evaluates whether children delivered what was asked
- **Metrics tracked** — budget efficiency, purpose alignment, evidence quality

### EQUIP (Workspace Prep)
Runs before exploration. Analyzes the catalog, computes field distributions, and writes a SKILL.md with:
- Corpus orientation and schema
- **Partitioning Guide** — field distributions with percentiles, segment counts, and ready-to-use partition schemes
- Partition rules (partitions vs lenses distinction)

### Catalog Step (AnalyticalSurvey)
Pure Python, zero LLM cost. Runs 10 independent techniques (basic stats, isolation forest, TF-IDF, DBSCAN, entity concentration, graph analysis, temporal, keywords, temporal text comparison, peer divergence). Produces numbered INVESTIGATION TARGETS with full evidence.

### Exploration
Engagement lead partitions the corpus into non-overlapping slices using record field filters (e.g., `dependency_count = 0`, `maintainer_count >= 2`). Each worker receives a distinct slice. Workers analyze their slice directly or sub-partition further. The MECE gate enforces tiling at every boundary.

### Partition-Based Data Routing
The engagement lead authors partitions (data filters), not lenses (analytical questions). EQUIP's translator converts natural-language partition descriptions to SQL against the enriched catalog. Workers receive records from their partition.

### Budget Management
- Shared atomic BudgetPool prevents parallel overspend
- Exploration has a hard cap (85% of total in role-authoring path)
- Downstream floor: min($0.30, total * 6%) reserved for synthesis/validation/impact
- Checked before every LLM call
- Remaining budget flows to synthesis, validation, and impact

## Tech Stack

- Python 3.14+ (python3, NOT python)
- `anthropic` SDK — uses `claude-sonnet-4-20250514` for all reasoning
- `httpx` for async HTTP
- `websockets` for real-time visualizer
- `sklearn`, `pandas`, `numpy` for analytical survey
- No databases, no vector stores, no RAG — just API calls and reasoning

## Key Files

- `run.py` — CLI entry point (--source, --budget, --visualize, --playback, --partition-gate)
- `mycelium/schemas.py` — all data structures (Directive with partition, Observation with evidence packets, BudgetPool)
- `mycelium/prompts_v2.py` — all LLM prompts for role-authoring path (centralized, single source of truth)
- `mycelium/prompts.py` — legacy v1 prompts
- `mycelium/survey.py` — AnalyticalSurvey (10 techniques, domain-agnostic)
- `mycelium/genesis.py` — charter generation from corpus metadata
- `mycelium/equip.py` — EQUIP workspace prep (field distributions, partition guide, SKILL.md)
- `mycelium/translator.py` — converts natural-language partition descriptions to SQL
- `mycelium/partition_gate.py` — MECE enforcement at every parent→child boundary
- `mycelium/worker_v2.py` — RoleWorkerNode (unified node: investigate, hire, reassess, Turn 2)
- `mycelium/worker.py` — legacy WorkerNode
- `mycelium/node.py` — legacy single-call reasoning primitive
- `mycelium/orchestrator.py` — full pipeline coordinator + run metrics + diagnostics
- `mycelium/bulletin_board.py` — lateral comms (post/pull between sibling nodes)
- `mycelium/synthesizer.py` — cross-referencing attention mechanism
- `mycelium/validator.py` — skeptical review (factual vs inferential + charter-shape check)
- `mycelium/significance.py` — novelty + actionability scoring
- `mycelium/impact.py` — real-world impact assessment
- `mycelium/reporter.py` — five-tier markdown report
- `mycelium/events.py` — WebSocket + events.jsonl recording
- `mycelium/knowledge_graph.py` — SQLite-backed persistent graph
- `mycelium/deliverable.py` — deliverable.db generation with embeddings
- `mycelium/obsidian_export.py` — Obsidian vault export (per-entity markdown with wiki-links)
- `visualizer.html` — D3.js real-time tree with reasoning display + playback

## Data Source Connector Pattern

Each data source implements this interface:

```python
class MyDataSource(DataSource):
    async def survey(self, filters: dict) -> dict:
        """Return ecosystem shape and metadata"""
    
    async def fetch(self, filters: dict, max_results: int) -> list[dict]:
        """Return records for a node to analyze"""
    
    async def fetch_bulk_metadata(self, max_records: int,
                                   progress_callback=None) -> list[dict]:
        """Return all accessible records for AnalyticalSurvey"""
    
    async def close(self):
        """Clean up connections"""
```

Current connectors:
- `mycelium/data_sources/npm_registry.py` — npm (public, no key, cached to catalog/npm_enriched.jsonl)
- `mycelium/data_sources/sec_edgar.py` — SEC EDGAR (public, User-Agent header, cached to catalog/sec_enriched.jsonl)
- `mycelium/data_sources/federal_register.py` — Federal Register (public, no key)

## Running

```bash
export ANTHROPIC_API_KEY=your_key

# Explore with live visualizer and MECE gate
python3 run.py --source npm --budget 10 --prompts v2 --visualize

# Headless run
python3 run.py --source npm --budget 5 --prompts v2

# Gate off (instrumentation only, no halt)
python3 run.py --source npm --budget 5 --prompts v2 --partition-gate off

# Replay a recorded run
python3 run.py --playback output/{run_id}/events.jsonl --speed 10
```

Output goes to `output/{run_id}/` with `report.md`, `metrics.json`, `tree.json`, `events.jsonl`, `knowledge_graph.json`, per-node `nodes/`, `diagnostics/`, `transcripts/`, and `translations/`.

## Output Structure

Each run produces:
- `report.md` — five-tier findings (Common Knowledge → Cross-Cutting Patterns)
- `metrics.json` — cost, quality, efficiency, token, coverage metrics
- `diagnostics/` — per-node input/output logs showing evidence flow
- `diagnostics/partition_gate/` — MECE gate results per parent node
- `translations/` — SQL translations of partition descriptions
- `transcripts/` — per-node markdown reasoning traces
- `workspace/` — charter.md, SKILL.md (shared context)
- `catalog/run_history.jsonl` — cumulative run-over-run comparison

## Cost Model

- Sonnet: $3/M input, $15/M output tokens
- Extended thinking: 8000 token budget per node
- Typical $1 run: ~5 nodes, ~26 observations, depth 1 (partition gate test)
- Typical $5 run: ~20 nodes, ~100 observations, depth 2-3
- Typical $10 run: ~45 nodes, ~180 observations, depth 3-4
- Run metrics track cost per observation, cost per validated finding, budget waste

## Conventions

- All prompts live in `prompts_v2.py` — never inline prompts elsewhere
- Every observation must be an evidence packet with specific data citations
- Node reasoning logged to `output/{run_id}/nodes/` and `transcripts/` for transparency
- All events recorded to `events.jsonl` for playback
- Anti-spin: single-child test, chain test, value test
- Chain circuit breaker: MAX_CHAIN_DEPTH=8 (the ONE hardcoded safety check)
- MECE partition gate: enforces corpus tiling at every parent→child boundary
- No domain-specific logic outside data source connectors
- Enrichment caches to `catalog/` — don't re-fetch what's already downloaded
