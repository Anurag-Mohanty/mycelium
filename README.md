# Mycelium — Engineered Curiosity

Release an autonomous explorer into any information space with a budget. It decides what to explore, how deep to go, and what to report. You get back findings that no single search or model pass could produce.

**What it does differently:** RAG retrieves. LLMs comprehend. Agents execute. Mycelium discovers — it finds things nobody knew to look for.

## What It Found

**SEC EDGAR** (26,495 filings from 6,966 companies, $5 budget): Extreme Networks eliminated 100% of risk factor disclosures between 2022 and 2023 (13,028 words → 0). Flagstar Bank's 2023 filing uses NYCB accession numbers and URL patterns (`nycb-20221231.htm`), revealing a corporate acquisition mid-filing-cycle. Computer Communications Equipment companies (SIC 3576) showed coordinated 77% reductions in risk disclosures. Found by cross-referencing 10-K filings across years, accession numbers, and SIC codes.

**npm Registry** (100,726 packages, $10 budget): Jon Schlinkert controls 1+ billion monthly downloads across 3 utility packages — a single individual as critical infrastructure. React core packages show synchronized version patterns with identical git hashes and timestamps across packages, proving automated CI/CD publishing rather than API instability. @flmngr packages exhibit artificial download inflation patterns across coordinated namespace. Found by analyzing metadata, maintainer concentration, and version patterns across 100K enriched packages.

These findings require reading primary data and comparing across entities and time. No web search produces them.

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key

# Explore npm packages ($5-10 recommended)
python3 run.py --source npm --budget 10 --visualize

# Explore SEC filings
python3 run.py --source sec --budget 5 --visualize

# Use v2 prompts (budget-aware reasoning, self-assessment, continuation funding)
python3 run.py --source npm --budget 10 --prompts v2

# Headless run
python3 run.py --source npm --budget 5

# Replay a recorded run
python3 run.py --playback output/{run_id}/events.jsonl --speed 10
```

## How It Works

```
CATALOG (free)      Statistical survey of all records. 10 analytical techniques.
                    Identifies multi-flagged anomalies before spending any AI budget.
                    Produces INVESTIGATION TARGETS with statistical evidence.
     |
GENESIS ($0.05)     LLM surveys corpus shape, generates attention lenses.
     |
PLANNER ($0.05)     Budget-aware exploration strategy. Reasons about exploration
                    envelope (40-75% of budget) and max decomposition depth from
                    leaf viability math.
     |
EXPLORE ($$$)       WorkerNode agents — persistent, multi-turn, self-decomposing.
  |   |   |         Each receives PURPOSE + TARGETS + DATA + BUDGET CONTEXT.
  |   |   |         Produces EVIDENCE PACKETS with signal strength classification.
  |   |   |         Self-assesses: follow-up threads, capability gaps, adjacent findings.
  |   |   |         Parent reviews and deploys remaining budget to continuations.
  |   |   |
SYNTHESIZE          Cross-references findings across branches.
     |
DEEP-DIVE           Targeted follow-up on most interesting findings.
     |
VALIDATE            Skeptical review of Tier 3-5 findings.
     |
IMPACT              Real-world consequence assessment.
     |
REPORT              Five-tier markdown report + run metrics + full transcript.
```

Every node runs: **Survey, Orient, Hypothesize, Assess Coverage, Produce**. Nodes receive a purpose, self-evaluate their output, and parents review whether children delivered what was asked. The code is plumbing. The LLM makes all decisions.

## Node Accountability

Every node operates like an employee in an organization:

- **Receives PURPOSE** — not just scope and data, but why it's being asked and how it fits the broader investigation
- **Receives BUDGET CONTEXT** — own envelope, parent's remaining pool, phase remaining, depth position, minimum child envelope
- **Produces EVIDENCE PACKETS** — structured data (raw_evidence, statistical_grounding, local_hypothesis, surprising_because), not prose summaries
- **Classifies SIGNAL STRENGTH** — each observation marked as `data_originated` (required reading the data) or `confirmatory` (an informed observer would have expected this)
- **Self-assesses** — purpose addressed, evidence quality, worthwhile follow-up threads, capability gaps, adjacent findings outside scope
- **Parent reviews (Turn 2)** — five-option budget deployment: fund continuation on flagged thread, fund adjacent finding, spawn more, pivot, or resolve
- **Metrics tracked** — budget efficiency, purpose alignment, evidence quality, envelope utilization per node

## Budget Architecture (v2 prompts)

- **Planner-determined exploration envelope** — 40-75% of total budget, reasoned from corpus complexity
- **Planner-computed max depth** — derived from leaf viability math, not arbitrary limits
- **Per-node envelope caps** — children can't silently overspend their allocation
- **Envelope floor** — children below minimum viable cost ($0.12) are rejected at spawn
- **Review phase** — separate 15% budget for Turn 2 reviews, independent of exploration surplus
- **Continuation funding** — parents deploy unspent envelope to follow-up children via Option A

## Data Sources

| Source | Coverage | Auth | Enrichment |
|--------|----------|------|------------|
| npm Registry | 3.97M packages, 100K enriched with full metadata | None | Cached (~4 hours first time) |
| SEC EDGAR | 26,495 10-K filings from 6,966 companies, risk factor extraction across 2021-2026 | None (User-Agent) | Cached (~50 min first time) |
| Federal Register | US federal regulations | None | On-demand |

Adding a new source: implement `survey()`, `fetch()`, `fetch_bulk_metadata()`, and `close()`. See `mycelium/data_sources/base.py`.

## Full Registry Catalog

For complete coverage (not just search API samples):

```bash
# One-time: download all 3.97M npm package names + download counts (~4 hours)
python3 catalog.py --source npm --full

# Resume if interrupted
python3 catalog.py --source npm --resume
```

The catalog filters to ~100K active packages (>1000 monthly downloads), enriches with full metadata, and runs the analytical survey. Future exploration runs automatically use the catalog if it exists.

SEC EDGAR enrichment happens automatically on first run — fetches all 10-K filings from 2021-2026, extracts risk factor sections, and caches to `catalog/sec_enriched.jsonl`.

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
9. **Temporal text comparison** — cosine similarity between consecutive filings, differentiating terms extracted at comparison time
10. **Peer divergence** — terms used by 80%+ of peers but absent from outliers

Records flagged by 2+ techniques become numbered **INVESTIGATION TARGETS** with full evidence (z-scores, differentiating terms, peer comparisons). Agents receive these as their primary job — explain why the math flagged it.

## Architecture

```
mycelium/
  orchestrator.py      # Full pipeline coordinator + run metrics + envelope enforcement
  worker.py            # WorkerNode — persistent multi-turn agent with envelope caps
  node.py              # Single-call reasoning primitive (legacy)
  genesis.py           # Corpus survey + lens generation
  planner.py           # Budget-aware strategy + exploration envelope + depth computation
  survey.py            # AnalyticalSurvey (10 techniques, sklearn/pandas)
  synthesizer.py       # Cross-reference sibling observations
  validator.py         # Skeptical review
  significance.py      # Novelty + actionability scoring
  impact.py            # Real-world impact assessment
  reporter.py          # Five-tier markdown report
  prompts.py           # Prompt version dispatcher (v1/v2)
  prompts_v1.py        # Original prompts (baseline, never modified)
  prompts_v2.py        # v2 prompts (budget-aware reasoning, self-assessment, 5-option Turn 2)
  schemas.py           # All data structures (Directive, BudgetPool with phase limits)
  events.py            # WebSocket + events.jsonl recording
  knowledge_graph.py   # SQLite-backed persistent graph
  data_sources/
    base.py            # DataSource interface + filter_schema()
    npm_registry.py    # npm (public, no key)
    sec_edgar.py       # SEC EDGAR (public, User-Agent header)
    federal_register.py
run.py                 # CLI entry point (--source, --budget, --prompts v1|v2, --visualize)
catalog.py             # Full registry catalog builder
build_transcripts.py   # Per-node + combined transcripts + dashboards
visualizer.html        # D3.js real-time tree visualization
```

## Output

Each run produces `output/{run_id}/` containing:

- `report.md` — Five-tier markdown report (Common Knowledge, Structural Insights, Contradictions, Gaps, Cross-Cutting Patterns)
- `metrics.json` — Cost, quality, efficiency, token usage, planner decisions, data coverage
- `full_transcript.md` — Combined transcript of all nodes in tree order (one file, scrollable)
- `dashboard.md` — Run summary with node index and diagnostic aggregates
- `tree.json` — Full exploration tree with all observations
- `events.jsonl` — Event stream for playback
- `knowledge_graph.json` — Entity/relationship graph
- `nodes/` — Per-node JSON (observations, thinking, Turn 2 review, self-assessment, metrics)
- `diagnostics/` — Per-node diagnostic logs (input data, targets, output quality, envelope, rejections)
- `transcripts/` — Per-node markdown transcripts

Run history is appended to `catalog/run_history.jsonl` for cross-run comparison. Generate transcripts for any run with `python3 build_transcripts.py {run_id}`.

## Diagnostics

Every run prints a **NODE DIAGNOSTIC SUMMARY** showing where signal flows and where it's lost:

```
NODE DIAGNOSTIC SUMMARY:
  Total nodes: 46
  Nodes with 0 observations: 1 (2%)
  Nodes that received anomaly targets: 45 (97%)
  Nodes whose targets included evidence: 42 (91%)
  Nodes that decomposed: 15 (32%)
  Nodes where self-eval flagged gaps: 13 (28%)
  Observations citing evidence: 175/180
```

Per-node diagnostics at `output/{run_id}/diagnostics/` show exactly what each node received (scope, purpose, data, targets) and what it produced (observations, decision, self-evaluation).

## Cost

- Model: Claude Sonnet ($3/M input, $15/M output)
- Extended thinking: 5000 token budget per node
- Typical $5 run (v2): ~20 nodes, ~50-60 observations, depth 2, 70-75% utilization
- Typical $10 run (v2): ~50 nodes, ~70-100 observations, depth 2-3, 75-85% utilization
- Run metrics track cost per observation, cost per validated finding, envelope utilization, spawn rejections

## Core Principle

**The code is plumbing. The LLM is the brain.** Nothing in the Python code is specific to any data corpus. No npm logic in prompts. No SEC knowledge in the survey engine. The system works identically on any data source. If you find `if field == "downloads"` anywhere except inside a connector, it's a bug.
