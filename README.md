# Mycelium — Engineered Curiosity

Release an autonomous explorer into any information space with a budget. It decides what to explore, how deep to go, and what to report. You get back findings that no single search or model pass could produce.

**What it does differently:** RAG retrieves. LLMs comprehend. Agents execute. Mycelium discovers — it finds things nobody knew to look for.

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key

# Explore npm packages ($5-10 recommended)
python3 run.py --source npm --budget 10 --prompts v2 --visualize

# Explore SEC filings
python3 run.py --source sec --budget 5 --prompts v2 --visualize

# Headless run with MECE partition gate
python3 run.py --source npm --budget 5 --prompts v2

# Gate off (instrumentation only, run continues even if partitions overlap)
python3 run.py --source npm --budget 5 --prompts v2 --partition-gate off

# Replay a recorded run
python3 run.py --playback output/{run_id}/events.jsonl --speed 10
```

## How It Works

```
CATALOG (free)      Statistical survey of all records. 10 analytical techniques.
                    100K+ records analyzed. Zero LLM cost.
     |
GENESIS ($0.15)     Organizational charter — mission, quality standards, stakes.
                    Reads survey results to ground the investigation.
     |
EQUIP ($0.03)       Workspace prep: field distributions, partition guide, SKILL.md.
                    Gives the engagement lead the corpus structure it needs to partition.
     |
EXPLORE ($$$)       Engagement lead partitions the corpus into non-overlapping slices:
                    - Each hire gets a data filter (e.g., "dependency_count = 0")
                    - MECE gate verifies partitions tile the corpus before children run
                    - Workers investigate their slice, sub-partition if needed
                    - Manager Turn 2 evaluates reasoning quality, spawns continuations
                    - Budget arithmetic gates every decision
     |
SYNTHESIZE          Cross-references findings across branches.
     |
DEEP-DIVE           Targeted follow-up on most interesting findings.
     |
VALIDATE            Skeptical review — confirmed, weakened, or refuted.
                    Charter-shape check catches findings matching excluded patterns.
     |
SIGNIFICANCE        Headlines vs significant vs noted.
     |
IMPACT              Real-world consequence assessment.
     |
REPORT              Five-tier markdown report + full diagnostics.
```

## Architecture: The Unified Node

Every node in the system is the same primitive. "Manager" and "worker" are descriptive labels applied after the fact — not structural categories. Every node runs the same assessment:

**Formation:** Floor test (is delegation overhead justified?) + Ceiling test (does scope exceed single-pass capacity at required depth?). The outcome — investigate alone or hire a team — emerges from the test results.

**Mid-investigation reassessment:** After producing initial observations, re-run the floor/ceiling tests with new information. Three outcomes emerge:
- **RESOLVE** — work is done, or data is honestly exhausted
- **INVESTIGATE_FURTHER** — take another reasoning turn to push deeper
- **HIRE** — scope revealed dimensions needing different cognition

**Manager Turn 2:** When a node hires, it evaluates each hire's reasoning quality (not budget consumed or observation count):
- **MET_COMMITTED** — grounded observations, real engagement evident
- **EXHAUSTED_COMMITTED** — honest conclusion that further work isn't warranted, with grounded reasoning
- **POOR_REASONING_UNDERFIRED** — thin work, thin reasoning, needs redo

**Role Definition:** Every hire receives a role authored by its manager:
- **Name** — the kind of cognition, not a topic label
- **Mission** — what excellent work looks like (the aspiration)
- **Bar** — minimum acceptable output (the floor, below which is failure)
- **Heuristic** — posture for ambiguous moments

**Data partitioning:** The engagement lead partitions the corpus into non-overlapping slices using record field filters (e.g., `dependency_count = 0`, `maintainer_count >= 2`). EQUIP provides field distributions so the engagement lead knows where natural break points are. The MECE partition gate verifies every partition set tiles its parent scope before children execute. Different slices produce divergent findings because workers examine genuinely different data.

## Budget System

- **BudgetPool** — shared atomic pool with phase limits
- **Exploration cap** — 85% of total budget, hard limit
- **Per-node envelope** — allocated by parent, minimum $0.12
- **Turn 2 arithmetic** — observable cost data (avg hire cost, downstream reservation, affordable hires)
- **Reasoning quality, not budget consumed** — a hire spending $0.20 of $1.00 with grounded reasoning is COMMITTED

## Quality Pipeline

Every finding passes through:

1. **Bar evaluation** — does it meet the authored bar?
2. **Synthesis** — cross-referencing across branches
3. **Validation** — skeptical review (confirmed / weakened / refuted)
4. **Reader test** — dual scoring: factual novelty + interpretive certainty
5. **Significance gate** — HEADLINE / SIGNIFICANT / NOTED
6. **Impact analysis** — real-world consequences

## Data Sources

| Source | Coverage | Auth | Enrichment |
|--------|----------|------|------------|
| npm Registry | 3.97M packages, 100K enriched with full metadata | None | Cached (~4 hours first time) |
| SEC EDGAR | 26,495 10-K filings from 6,966 companies | None (User-Agent) | Cached (~50 min first time) |
| Federal Register | US federal regulations | None | On-demand |

Adding a new source: implement `survey()`, `fetch()`, `fetch_bulk_metadata()`, `filter_schema()`, and `close()`. See `mycelium/data_sources/base.py`.

## Analytical Survey

Before spending any AI budget, the `AnalyticalSurvey` runs 10 independent techniques on all records:

1. **Basic statistics** — distributions, z-score outliers, concentrations
2. **Isolation Forest** — multi-dimensional outlier detection
3. **TF-IDF** — records with unusual text content
4. **DBSCAN clustering** — records that don't belong to any cluster
5. **Entity concentration** — entities with outsized influence
6. **Graph analysis** — centrality, dependency inversions, orphans
7. **Temporal analysis** — stale-but-active, velocity anomalies
8. **Keyword signals** — uncommon keywords correlating with extreme values
9. **Temporal text comparison** — cosine similarity between consecutive filings
10. **Peer divergence** — terms used by 80%+ of peers but absent from outliers

Records flagged by 2+ techniques become numbered **INVESTIGATION TARGETS** with full evidence.

## Project Structure

```
mycelium/
  orchestrator.py      # Full pipeline coordinator
  worker_v2.py         # Unified node — investigate, reassess, extend, hire, Turn 2
  schemas.py           # RoleDefinition, Directive, BudgetPool, Observation
  prompts_v2.py        # All LLM prompts (single source of truth)
  genesis.py           # Charter generation
  equip.py             # EQUIP workspace prep (field distributions, partition guide)
  translator.py        # Natural-language partition → SQL translation
  partition_gate.py    # MECE enforcement at every parent→child boundary
  bulletin_board.py    # Lateral comms (post/pull between sibling nodes)
  workspace.py         # Filesystem workspace (charter.md, rules.md)
  survey.py            # AnalyticalSurvey (10 techniques)
  synthesizer.py       # Cross-reference sibling observations
  validator.py         # Skeptical review (factual + interpretive + charter-shape)
  significance.py      # Novelty + actionability scoring
  impact.py            # Real-world impact assessment
  reporter.py          # Five-tier markdown report
  reader_test.py       # Per-finding quality gate
  events.py            # WebSocket + events.jsonl recording
  knowledge_graph.py   # SQLite-backed persistent graph
  deliverable.py       # Deliverable DB with embeddings
  obsidian_export.py   # Obsidian vault export
  data_sources/
    base.py            # DataSource interface + filter_schema() + query_catalog()
    npm_registry.py    # npm connector
    sec_edgar.py       # SEC EDGAR connector
    federal_register.py
run.py                 # CLI entry point
visualizer.html        # D3.js real-time tree visualization
docs/
  PRD.md               # Full product requirements document and operational manual
  AGENTIC_LESSONS.md   # Failure modes discovered building autonomous systems
```

## Output

Each run produces `output/{run_id}/` containing:

- `report.md` — Five-tier findings report
- `metrics.json` — Cost, quality, efficiency metrics
- `full_transcript.md` — Combined transcript of all nodes
- `full_diagnostic.txt` — Per-node input/output diagnostic
- `dashboard.md` — Run summary with node index
- `tree.json` — Full exploration tree
- `events.jsonl` — Event stream for playback
- `knowledge_graph.json` — Entity/relationship graph
- `workspace/` — Charter, rules, and SKILL.md (shared context)
- `nodes/` — Per-node JSON with observations, thinking, Turn 2 results
- `diagnostics/` — Per-node diagnostic logs
- `diagnostics/partition_gate/` — MECE gate results per parent node
- `translations/` — SQL translations of partition descriptions
- `transcripts/` — Per-node markdown transcripts
- `deliverable.db` — SQLite deliverable with embeddings
- `obsidian_vault/` — Per-entity markdown files with wiki-links

## Cost

- Model: Claude Sonnet ($3/M input, $15/M output)
- Extended thinking: 8000 token budget per node
- Typical $1 run: ~5 nodes, ~26 observations, depth 1 (partition gate validation)
- Typical $5 run: ~20 nodes, ~100 observations, depth 2-3
- Typical $10 run: ~45 nodes, ~180 observations, depth 3-4

## Core Principle

**The code is plumbing. The LLM is the brain.** Nothing in the Python code is specific to any data corpus. No npm logic in prompts. No SEC knowledge in the survey engine. The system works identically on any data source. If you find `if field == "downloads"` anywhere except inside a connector, it's a bug.

## Documentation

See [docs/PRD.md](docs/PRD.md) for the full product requirements document and operational manual covering every pipeline step, glossary of terms, metrics reference, and data source connector pattern.
