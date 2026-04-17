# Mycelium — Recursive Knowledge Discovery Framework

Mycelium **discovers unknown knowledge** in information spaces too large for any single model pass. It doesn't answer questions. It doesn't search. It explores — and produces both findings and questions that nobody knew to ask.

**How it differs:**
- **RAG** retrieves. **LLMs** comprehend. **Agents** execute. **Mycelium** discovers.

## The Analogy

An evidence room collecting dust for decades. You walk in. You don't know if you're looking for a serial killer, bank fraud, or corruption. You just know things are hiding underneath.

A detective doesn't start with a question. They look, notice patterns, get curious, pull threads, form hypotheses they didn't have when they walked in. That's what each Mycelium node does.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=your_key_here

# Run an exploration
python run.py --source federal_register --lens "fraud"

# With constraints
python run.py --source federal_register --lens "fraud" \
    --max-depth 3 --max-nodes 30 --cost-limit 10.0

# Focus on specific agencies
python run.py --source federal_register --agencies FTC SEC --lens "enforcement"
```

## Architecture

```
GENESIS NODE (runs once)
│ Surveys corpus SHAPE (not content)
│ Generates 10-15 attention lenses
│
ROOT NODE
│ Surveys full space → "too broad" → identifies divisions
│
├── DOMAIN NODE A
│   ├── LEAF NODE → observations
│   └── LEAF NODE → observations
│   ↑ SYNTHESIS: cross-references, finds patterns
│
├── DOMAIN NODE B
│   └── ...
│
↑ ROOT SYNTHESIS: full knowledge graph, discovered questions
```

Every node runs the same 5-step loop: **Survey → Orient → Hypothesize → Assess → Produce**

## Output

Reports are organized in five tiers:

1. **Common Knowledge** — confirmed facts (builds credibility)
2. **Structural Insights** — how the space is actually organized
3. **Contradictions** — sources that say opposite things
4. **Gaps** — what should be there but isn't
5. **Cross-Cutting Patterns** — connections requiring recursive exploration

Plus: **Discovered Questions** that emerged from the data, and **Unresolved Threads** for further investigation.

## Project Structure

```
mycelium/
├── mycelium/
│   ├── genesis.py          # Lens generation from corpus shape
│   ├── node.py             # Core 5-step reasoning primitive
│   ├── orchestrator.py     # Recursive tree management
│   ├── synthesizer.py      # Cross-referencing / attention
│   ├── reporter.py         # Five-tier report generation
│   ├── prompts.py          # All LLM prompts (centralized)
│   ├── schemas.py          # Data structures
│   └── data_sources/
│       ├── base.py         # DataSource interface
│       └── federal_register.py  # Federal Register API
├── output/                 # Exploration results
├── run.py                  # Entry point
└── requirements.txt
```

## Cost

Each exploration run logs token usage and cost in real-time. Default cost limit is $20. A typical 3-depth, 30-node run costs $2-8 depending on corpus density.
