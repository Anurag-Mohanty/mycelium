# Mycelium — Engineered Curiosity

## What This Is

An autonomous discovery framework that finds unknown patterns in any information space. Point it at a data source, give it a budget, and it decides what to explore, how deep to go, and what to report. Currently demonstrated on the npm registry (2.5M+ packages) and US Federal Register.

## Core Principle: Pure Agentic, Zero Hardcoding

**The code is plumbing. The LLM is the brain.**

NOTHING in the Python code is specific to any data corpus. No npm-specific logic in prompts. No field-name matching in the survey engine. No domain knowledge in the orchestrator. The system must work identically whether pointed at:
- npm registry (software packages)
- Hospital patient records (FHIR/HL7)
- Pollution monitoring stations
- SEC EDGAR financial filings
- CNN news archives
- A company's Jira tickets

If you find yourself writing `if field == "downloads"` or `if source == "npm"` anywhere except inside a data source connector, you are violating this principle. The connectors adapt external APIs to a common interface. Everything else is domain-agnostic.

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
Catalog (free) → Genesis → Planner → Explore (parallel) → Synthesis → Deep-dive → Validate → Significance → Impact → Report

### Catalog Step (ProgrammaticSurvey)
Pure Python, zero LLM cost. Scans all accessible records, computes distributions, outliers, concentrations, correlations, and anomaly clusters. Tells you what's interesting BEFORE committing any AI budget.

### Exploration
Every node runs the same 5-step loop: Survey → Orient → Hypothesize → Assess → Produce

Nodes decide autonomously whether to resolve (analyze directly) or decompose (delegate to children). Budget controls depth, not hardcoded limits.

### Budget Management
- Atomic reservation prevents parallel overspend
- Exploration has a hard cap (50% of total)
- Remaining budget flows to synthesis, validation, and impact
- Every pipeline phase executes — no phase at 0%

## Tech Stack

- Python 3.14+ (python3, NOT python)
- `anthropic` SDK — uses `claude-sonnet-4-20250514` for all reasoning
- `httpx` for async HTTP
- `websockets` for real-time visualizer
- No databases, no vector stores, no RAG — just API calls and reasoning

## Key Files

- `run.py` — CLI entry point (--source, --budget, --visualize, --playback)
- `mycelium/schemas.py` — all data structures + BudgetPool with atomic reservation
- `mycelium/prompts.py` — all LLM prompts (centralized, single source of truth)
- `mycelium/survey.py` — ProgrammaticSurvey (domain-agnostic statistics engine)
- `mycelium/genesis.py` — corpus shape survey + lens generation
- `mycelium/planner.py` — budget-aware exploration strategy
- `mycelium/node.py` — core reasoning primitive (one LLM call per node)
- `mycelium/synthesizer.py` — cross-referencing attention mechanism (light + full)
- `mycelium/orchestrator.py` — recursive tree + full pipeline
- `mycelium/validator.py` — skeptical review (factual vs inferential distinction)
- `mycelium/significance.py` — novelty + actionability scoring
- `mycelium/impact.py` — real-world impact assessment
- `mycelium/reporter.py` — five-tier markdown report
- `mycelium/events.py` — WebSocket + events.jsonl recording
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
        """Return all accessible records for ProgrammaticSurvey"""
    
    async def close(self):
        """Clean up connections"""
```

Current connectors:
- `mycelium/data_sources/npm_registry.py` — npm (public, no key)
- `mycelium/data_sources/federal_register.py` — Federal Register (public, no key)

## Running

```bash
export ANTHROPIC_API_KEY=your_key

# Explore with live visualizer
python3 run.py --source npm --budget 10 --visualize

# Headless run
python3 run.py --source npm --budget 10

# Replay a recorded run
python3 run.py --playback output/{run_id}/events.jsonl --speed 10

# Budget estimation
python3 run.py --source npm --estimate
```

Output goes to `output/{run_id}/` with `tree.json`, `report.md`, `events.jsonl`, `knowledge_graph.json`, and per-node reasoning logs.

## Cost Model

- Sonnet: $3/M input, $15/M output tokens
- Budget allocation: explore 50%, synthesis 18%, validation 7%, impact 10%, overhead 7%, deep-dive 8%
- Typical $10 run: ~130 nodes, ~600 observations, 20+ validated findings, 3+ significant

## Conventions

- All prompts live in `prompts.py` — never inline prompts elsewhere
- Every observation must cite a specific document (source-or-silence rule)
- Node reasoning is logged to `output/{run_id}/nodes/` for full transparency
- All events recorded to `events.jsonl` for playback
- Anti-spin principles: single-child test, chain test, value test
- Chain circuit breaker: MAX_CHAIN_DEPTH=8 (the ONE hardcoded safety check)
- No domain-specific logic outside data source connectors
