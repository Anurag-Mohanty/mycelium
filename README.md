# Mycelium — Engineered Curiosity

Release an autonomous explorer into any information space with a budget. It decides what to explore, how deep to go, and what to report. You get back findings that no single search or model pass could produce.

**What it does differently:** RAG retrieves. LLMs comprehend. Agents execute. Mycelium discovers — it finds things nobody knew to look for.

## What It Found

**SEC EDGAR** (3,891 filings, $5 budget): Berkshire Hathaway Energy expanded risk factors from 11,014 to 148,988 words citing "PacifiCorp litigation risks." Amplify Energy shrank from 66,155 to 34,381 after their pipeline incident resolved. XTI Aerospace files under SIC 7371 (Computer Programming) but discloses 22,492 words of aerospace risk content — SIC codes lag actual business models. Found by reading actual 10-K text across 3,886 companies.

**npm Registry** (100K packages, $10 budget): @vue/shared (80.9M downloads) outperforms the main vue package (46M) — internal utility exceeds its own framework. @segment/analytics-next has 335 maintainers while Vue core has 1. expo, Supabase, and @typescript-eslint/scope-manager all show 0 monthly downloads despite millions of real users — npm tracking system failures. Found by analyzing metadata across 100K enriched packages.

These findings require reading primary data and comparing across entities and time. No web search produces them.

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key

# Explore npm packages
python3 run.py --source npm --budget 10 --visualize

# Explore SEC filings
python3 run.py --source sec --budget 10 --visualize

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
PLANNER ($0.05)     Budget-aware exploration strategy. Segments with sub-budgets.
     |
EXPLORE ($$$)       WorkerNode agents — persistent, multi-turn, self-decomposing.
  |   |   |         Each receives PURPOSE + TARGETS + DATA.
  |   |   |         Produces EVIDENCE PACKETS, not prose summaries.
  |   |   |         Self-evaluates before reporting. Parent reviews alignment.
  |   |   |
SYNTHESIZE          Cross-references findings across branches.
     |
DEEP-DIVE           Targeted follow-up on most interesting findings.
     |
VALIDATE            Skeptical review of Tier 3-5 findings.
     |
IMPACT              Real-world consequence assessment.
     |
REPORT              Five-tier markdown report + run metrics.
```

Every node runs: **Survey, Orient, Hypothesize, Assess Coverage, Produce**. Nodes receive a purpose, self-evaluate their output, and parents review whether children delivered what was asked. The code is plumbing. The LLM makes all decisions.

## Node Accountability

Every node operates like an employee in an organization:

- **Receives PURPOSE** — not just scope and data, but why it's being asked and how it fits the broader investigation
- **Produces EVIDENCE PACKETS** — structured data (raw_evidence, statistical_grounding, local_hypothesis, surprising_because), not prose summaries
- **Self-evaluates** — before reporting, asks "did I address my purpose or miss the point?"
- **Parent reviews** — Turn 2 evaluates whether each child delivered what was needed
- **Metrics tracked** — budget efficiency, purpose alignment, evidence quality per node

## Data Sources

| Source | Coverage | Auth | Enrichment |
|--------|----------|------|------------|
| npm Registry | 3.97M packages, 100K enriched with full metadata | None | Cached (~4 hours first time) |
| SEC EDGAR | 41K+ 10-K filings, risk factor extraction across 2021-2026 | None (User-Agent) | Cached (~50 min first time) |
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
  orchestrator.py      # Full pipeline coordinator + run metrics
  worker.py            # WorkerNode — persistent multi-turn agent with diagnostics
  node.py              # Single-call reasoning primitive
  genesis.py           # Corpus survey + lens generation
  planner.py           # Budget-aware exploration strategy
  survey.py            # AnalyticalSurvey (10 techniques, sklearn/pandas)
  synthesizer.py       # Cross-reference sibling observations
  validator.py         # Skeptical review
  significance.py      # Novelty + actionability scoring
  impact.py            # Real-world impact assessment
  reporter.py          # Five-tier markdown report
  prompts.py           # All LLM prompts (centralized)
  schemas.py           # All data structures (Directive with purpose field)
  events.py            # WebSocket + events.jsonl recording
  knowledge_graph.py   # SQLite-backed persistent graph
  data_sources/
    base.py            # DataSource interface
    npm_registry.py    # npm (public, no key)
    sec_edgar.py       # SEC EDGAR (public, User-Agent header)
    federal_register.py
catalog.py             # Full registry catalog builder
visualizer.html        # D3.js real-time tree visualization
```

## Output

Each run produces `output/{run_id}/` containing:

- `report.md` — Five-tier markdown report (Common Knowledge, Structural Insights, Contradictions, Gaps, Cross-Cutting Patterns)
- `metrics.json` — Cost, quality, efficiency, token usage, data coverage metrics
- `tree.json` — Full exploration tree with all observations
- `events.jsonl` — Event stream for playback
- `knowledge_graph.json` — Entity/relationship graph
- `nodes/` — Per-node reasoning logs (full thinking + observations)
- `diagnostics/` — Per-node diagnostic logs (input data, targets received, output quality, budget)

Run history is appended to `catalog/run_history.jsonl` for cross-run comparison.

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
- Typical $5 run: ~30 nodes, ~60 observations, depth 3-4
- Typical $10 run: ~45 nodes, ~180 observations, depth 4+
- Run metrics track cost per observation, cost per validated finding, budget waste on zero-observation nodes

## Core Principle

**The code is plumbing. The LLM is the brain.** Nothing in the Python code is specific to any data corpus. No npm logic in prompts. No SEC knowledge in the survey engine. The system works identically on any data source. If you find `if field == "downloads"` anywhere except inside a connector, it's a bug.
