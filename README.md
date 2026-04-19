# Mycelium — Engineered Curiosity

Release an autonomous explorer into any information space with a budget. It decides what to explore, how deep to go, and what to report. You get back findings that no single search or model pass could produce.

**What it does differently:** RAG retrieves. LLMs comprehend. Agents execute. Mycelium discovers — it finds things nobody knew to look for.

## What It Found

**npm Registry** (3.97M packages): "3M expanded risk factors from 3,281 to 58,626 words after PFAS liability. Amplify Energy did the opposite — shrunk from 47,264 to 25,054 after their pipeline incident." Opposite crisis disclosure strategies, found by reading actual filing text across companies and years.

**SEC EDGAR** (41K+ 10-K filings): 373 nodes, 837 observations, depth 15. Agents read actual risk factor text from 50 companies across 5 years, compared them against peers and prior years, and flagged companies whose disclosures diverged from industry patterns.

These findings require reading primary data — actual package metadata, actual filing text — and comparing across entities and time. No web search produces them.

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key

# Explore npm packages
python3 run.py --source npm --budget 10 --visualize

# Explore SEC filings
python3 run.py --source sec --budget 5 --visualize

# Headless run
python3 run.py --source npm --budget 5

# Replay a recorded run
python3 run.py --playback output/{run_id}/events.jsonl --speed 10
```

## How It Works

```
CATALOG (free)      Statistical survey of all records. 10 analytical techniques.
                    Identifies multi-flagged anomalies before spending any AI budget.
     |
GENESIS ($0.05)     LLM surveys corpus shape, generates attention lenses.
     |
PLANNER ($0.05)     Budget-aware exploration strategy. Segments with sub-budgets.
     |
EXPLORE ($$$)       WorkerNode agents — persistent, multi-turn, self-decomposing.
  |   |   |         Each decides: analyze myself, or hire specialists to go deeper.
  |   |   |         Depth is emergent, not prescribed.
  |   |   |
SYNTHESIZE          Cross-references findings across branches.
     |
VALIDATE            Skeptical review of Tier 3-5 findings.
     |
IMPACT              Real-world consequence assessment.
     |
REPORT              Five-tier markdown report.
```

Every node runs the same 5-step loop: **Survey, Orient, Hypothesize, Assess, Produce**. The code is plumbing. The LLM makes all decisions.

## Data Sources

| Source | Coverage | Auth |
|--------|----------|------|
| npm Registry | 3.97M packages, full download counts | None |
| SEC EDGAR | 41K+ 10-K filings, risk factor extraction | None (User-Agent header) |
| Federal Register | US federal regulations | None |

Adding a new source: implement `survey()`, `fetch()`, `fetch_bulk_metadata()`, and `close()`. See `mycelium/data_sources/base.py`.

## Full Registry Catalog

For complete coverage (not just search API samples):

```bash
# One-time: download all 3.97M npm package names + download counts (~4 hours)
python3 catalog.py --source npm --full

# Resume if interrupted
python3 catalog.py --source npm --resume
```

The catalog filters to ~75K active packages (>1000 monthly downloads), enriches with full metadata, and runs the analytical survey. Future exploration runs automatically use the catalog if it exists.

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
9. **Temporal text comparison** — cosine similarity between consecutive filings, term removal tracking
10. **Peer divergence** — terms used by 80%+ of peers but absent from outliers

Records flagged by 2+ techniques are highest-priority investigation targets. The LLM doesn't choose where to look — math already identified what's unusual.

## Architecture

```
mycelium/
  orchestrator.py      # Full pipeline coordinator
  worker.py            # WorkerNode — persistent multi-turn agent
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
  schemas.py           # All data structures
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

Reports are organized in five tiers:

1. **Common Knowledge** — confirmed facts
2. **Structural Insights** — how the space is organized
3. **Contradictions** — sources that conflict
4. **Gaps** — what should be there but isn't
5. **Cross-Cutting Patterns** — connections across domains

Plus: **Discovered Questions** and **Unresolved Threads** for further investigation.

Output goes to `output/{run_id}/` with `report.md`, `tree.json`, `events.jsonl`, `knowledge_graph.json`, and per-node reasoning logs.

## Cost

- Model: Claude Sonnet ($3/M input, $15/M output)
- Extended thinking: 5000 token budget per node
- Typical $5 run: ~60 nodes, ~100 observations, full pipeline
- Typical $10 run: ~150 nodes, ~600 observations, depth 5+

## Core Principle

**The code is plumbing. The LLM is the brain.** Nothing in the Python code is specific to any data corpus. No npm logic in prompts. No SEC knowledge in the survey engine. The system works identically on any data source. If you find `if field == "downloads"` anywhere except inside a connector, it's a bug.
