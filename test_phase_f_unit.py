"""Phase F Milestone 1 — Unit tests for Genesis charter and Planner operational plan.

Runs three unit tests:
1. Genesis produces a charter in CEO directive voice
2. Planner derives rules of engagement from the charter
3. Planner produces initial scopes derived from the charter

Uses real npm survey data. Saves outputs for human review.
"""

import asyncio
import json
import random
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# Output directory for review
OUT_DIR = Path("output/phase_f_unit_tests")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --- New Genesis Charter Prompt ---

CHARTER_PROMPT = """\
You are the CEO of a research organization. You are about to send your entire \
team into an information space to find things nobody knows yet.

You have received a structural survey of the corpus and a statistical analysis \
of what patterns the data contains. You have also received a briefing on what \
domain practitioners already know about this space.

TODAY'S DATE: {current_date}
(Timestamps from the current year are normal — packages are actively maintained. \
Do NOT frame current-year dates as anomalous or impossible.)

CORPUS METADATA (sample of records):
{corpus_metadata}

STATISTICAL SURVEY FINDINGS:
{survey_findings}

COMMON KNOWLEDGE BRIEFING:
{briefing}

TOTAL BUDGET: ${budget:.2f}

YOUR TASK: Write the ORGANIZATIONAL CHARTER for this investigation.

The charter is your directive to the entire organization. Every investigator \
will read it. It sets purpose, standards, and stakes. Write it in your voice \
as the leader — not as a report, not as a description of the data, but as a \
directive that tells your team what you expect from them.

The charter must cover:

1. WHAT WE ARE INVESTIGATING AND WHY IT MATTERS. Frame the corpus and the \
mission. Not "this dataset contains 100K packages" but "we have been given \
access to the complete dependency graph of the modern software ecosystem — \
every package, every maintainer, every version. Our job is to find what \
nobody else has noticed."

2. WHAT IS ALREADY KNOWN. Incorporate the briefing content. Tell your team \
what's common knowledge so they don't waste time rediscovering it. Be specific \
— name entities, cite numbers. "Everyone knows lodash is maintained by one \
person. Don't bring me that. Bring me what's BEHIND that — why the ecosystem \
tolerates it, what it implies for packages that depend on lodash, where the \
actual risk concentrates."

3. WHAT IMPRESSES US AND WHAT DOESN'T. Define the quality bar. What kind of \
finding would make leadership say "we didn't know that"? What kind would make \
them say "obvious"? Be concrete about the LEVEL OF SPECIFICITY you demand — \
named entities, exact numbers, traceable evidence. But do NOT constrain the \
KIND of novelty. Surprising findings can be about hidden vulnerabilities, \
but they can equally be about ecosystem dynamics, adoption patterns, community \
behavior, architectural anomalies, economic structures, or any other shape of \
hidden structure that emerges from the data. \
Generic category-level observations are not impressive ("many packages have \
single maintainers"). Specific discoveries with evidence ARE impressive, \
regardless of what dimension they're on — a hidden supply-chain chokepoint, \
a surprising adoption dynamic between competing frameworks, an unusual \
architectural pattern that reveals how the ecosystem actually evolves, a \
download flow anomaly that exposes how packages actually get used vs how \
they're marketed. The test is: would a knowledgeable practitioner say \
"I didn't know that"? Not: "does this fit a particular category of finding?"

4. WHAT THE STAKES ARE. Why does this investigation matter? What's the cost \
of missing something? What would a great investigation enable that a mediocre \
one wouldn't?

CONSTRAINTS:
- Write in directive voice. You are addressing your team, not writing a report.
- Do NOT list investigation areas or suggest where to look — that's the \
program office's job.
- Do NOT specify organizational structure — that's also the program office's job.
- DO incorporate the statistical findings and briefing into your framing — \
they inform what's already known and what gaps exist.
- Keep it under 800 words. Your team reads this before every quality decision. \
It needs to be memorable, not exhaustive.

Respond with ONLY the charter text. No JSON. No preamble. No metadata. \
Just the directive.
"""


# --- New Planner Operational Plan Prompt ---

OPERATIONAL_PLAN_PROMPT = """\
You are the program office for a research organization. The CEO has issued \
the organizational charter below. Your job is to translate this directive \
into operational reality.

ORGANIZATIONAL CHARTER:
{charter}

CORPUS SHAPE:
{corpus_shape}

TOTAL BUDGET: ${budget:.2f}
MINIMUM VIABLE WORKER ENVELOPE: ${leaf_viable_envelope:.2f}

The total budget covers EVERYTHING — investigation, synthesis, validation, \
impact analysis, report generation, and overhead. You decide how to split it.

YOUR TASK: Produce the operational plan for this investigation. The plan has \
two parts: rules of engagement, and initial scopes.

PART 1 — RULES OF ENGAGEMENT

Derive operational rules from the charter. These rules govern how every \
worker in the organization behaves. They are not generic best practices — \
they are specific policies that follow from THIS charter's directive.

Cover whatever the charter's directive requires. For an exploration charter, \
this likely includes:
- Budget policy: how workers should spend, when to go deep vs stay broad
- Budget allocation policy: downstream-phase allocations (synthesis, \
validation, etc.) are CEILINGS, not reservations. Unused budget flows \
back to the shared pool and becomes available for exploration. The pool \
enforces actual spending; allocations are upper-bound estimates.
- Evidence standards: what counts as sufficient evidence for a claim
- Depth policy: when decomposition is warranted vs when to resolve
- Quality bar: operational translation of the charter's "what impresses us"
- Novelty requirement: how workers should treat commonly-known patterns

Write rules that a worker at depth 5 could read and know exactly how to \
behave. Concrete, not aspirational.

PART 2 — INITIAL SCOPES

Divide the investigation into initial scopes for top-level workers. Each \
scope is a distinct area of investigation derived from the charter and \
corpus shape.

For each scope, determine its SCOPE LEVEL:
- "manager": The territory is too broad for one worker. The receiving \
worker will decompose immediately. Write the description in \
territory-ownership language: "This subtree owns investigation of X. \
Success at the subtree level means..." Help the manager reason about \
how to divide the territory, not how to investigate directly.
- "worker": A single worker can reasonably do this at the allocated \
budget. Write in direct investigation-instruction language.
- "ambiguous": The receiving worker should decide at runtime. Write \
the description so it works for both a direct investigator and a \
manager deciding whether to decompose.

For each scope:
- Scope level (manager / worker / ambiguous) with brief justification
- What it investigates (voice matches scope level as described above)
- Why it matters (traced back to the charter's directive)
- Budget allocation (must sum to exploration budget; proportional to \
expected complexity and charter priority)
- Success criteria (what would a good result look like for this scope?)

The number of scopes, what they cover, and how budget is distributed are \
YOUR derivations from the charter. A different charter would produce \
different scopes.

BUDGET MATH:
- Total budget: ${budget:.2f}
- You must allocate EVERY dollar. No unaccounted reserves.
- Scope budgets are for investigation work (workers exploring the corpus).
- Downstream phases (synthesis, validation, impact analysis, report \
generation, overhead from Genesis/Planner themselves) also cost money. \
Estimate what each needs and state it explicitly in your budget allocation.
- Each investigation scope's budget must be at least ${leaf_viable_envelope:.2f}
- All allocations must sum to the total budget.

Return JSON:
{{
    "rules_of_engagement": "the full rules text — written to be read by workers",
    "initial_scopes": [
        {{
            "name": "scope name",
            "scope_level": "manager | worker | ambiguous",
            "scope_level_reasoning": "why this level",
            "description": "what this scope investigates (voice matches scope level)",
            "charter_rationale": "why the charter demands this investigation",
            "budget": 0.00,
            "success_criteria": "what good output looks like"
        }}
    ],
    "budget_allocation": {{
        "investigation_total": 0.00,
        "synthesis": 0.00,
        "validation": 0.00,
        "impact_analysis": 0.00,
        "report_generation": 0.00,
        "overhead": 0.00,
        "reasoning": "why this split"
    }},
    "depth_policy": "summary of depth guidance from rules"
}}

Respond ONLY with valid JSON, no other text.
"""


def load_npm_survey_data():
    """Load real npm survey and catalog data for the unit tests."""
    # Load survey cache
    survey_path = Path("catalog/survey_cache_npm.json")
    survey_results = {}
    if survey_path.exists():
        with open(survey_path) as f:
            survey_results = json.load(f)

    # Load a sample of catalog records
    catalog_path = Path("catalog/npm_enriched.jsonl")
    records = []
    if catalog_path.exists():
        with open(catalog_path) as f:
            for line in f:
                records.append(json.loads(line))

    # Sample for Genesis
    sample_size = min(200, len(records))
    sample = random.sample(records, sample_size) if records else []
    lightweight_sample = []
    for rec in sample:
        light = {k: v for k, v in rec.items()
                 if k not in ("risk_factors_text", "abstract", "dependencies",
                              "dev_dependencies", "description")
                 or (isinstance(v, str) and len(v) < 200)}
        lightweight_sample.append(light)

    corpus_metadata = json.dumps({
        "source": "npm_registry",
        "total_packages": f"{len(records)} cataloged (sample of {sample_size} shown)",
        "scope": "broad ecosystem survey",
        "packages": lightweight_sample,
    }, indent=2)

    # Build survey findings string
    survey_findings = "No statistical survey available."
    if survey_results:
        lines = []
        lines.append(f"Records analyzed: {survey_results.get('record_count', '?')}")
        lines.append(f"Techniques applied: {', '.join(survey_results.get('techniques_applied', []))}")
        lines.append(f"Outliers found: {len(survey_results.get('outliers', []))}")
        lines.append(f"Concentrations: {len(survey_results.get('concentrations', []))}")
        clusters = survey_results.get("anomaly_clusters", [])
        if clusters:
            lines.append(f"Multi-flagged anomaly clusters: {len(clusters)}")
            for c in clusters[:10]:
                lines.append(f"  - [{c.get('severity', '?')}] {c.get('name', c.get('description', '?'))}")
        survey_findings = "\n".join(lines)

    return corpus_metadata, survey_findings, records


def load_existing_briefing():
    """Load the most recent npm briefing if available."""
    briefing_dir = Path("catalog/briefings")
    if briefing_dir.exists():
        npm_briefings = sorted(briefing_dir.glob("npm_*.txt"), reverse=True)
        if npm_briefings:
            return npm_briefings[0].read_text()
    # Fall back to generating a minimal one
    return "(No existing briefing available — use survey findings as proxy for common knowledge.)"


async def test_genesis_charter():
    """Test 1: Genesis produces a charter in CEO directive voice."""
    print("\n" + "="*60)
    print("TEST 1: Genesis Charter")
    print("="*60)

    corpus_metadata, survey_findings, records = load_npm_survey_data()
    briefing = load_existing_briefing()

    prompt = CHARTER_PROMPT.format(
        corpus_metadata=corpus_metadata,
        survey_findings=survey_findings,
        briefing=briefing,
        budget=10.0,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    charter = response.content[0].text
    usage = response.usage
    cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000

    # Save for review
    out_path = OUT_DIR / "01_charter.md"
    with open(out_path, "w") as f:
        f.write("# Genesis Charter Output\n\n")
        f.write(f"Cost: ${cost:.4f}\n")
        f.write(f"Input tokens: {usage.input_tokens}\n")
        f.write(f"Output tokens: {usage.output_tokens}\n\n")
        f.write("---\n\n")
        f.write(charter)

    print(f"\nCharter generated. Cost: ${cost:.4f}")
    print(f"Length: {len(charter)} chars, ~{len(charter.split())} words")
    print(f"Saved to: {out_path}")
    print(f"\nFirst 300 chars:\n{charter[:300]}...")

    return charter


async def test_planner_rules_and_scopes(charter: str):
    """Test 2+3: Planner derives rules of engagement and initial scopes from charter."""
    print("\n" + "="*60)
    print("TEST 2+3: Planner Rules of Engagement + Initial Scopes")
    print("="*60)

    corpus_metadata, survey_findings, records = load_npm_survey_data()

    # Build corpus shape summary for the planner
    corpus_shape = json.dumps({
        "total_records": len(records),
        "survey_findings": survey_findings,
    }, indent=2)

    budget = 10.0
    leaf_viable_envelope = 0.12

    prompt = OPERATIONAL_PLAN_PROMPT.format(
        charter=charter,
        corpus_shape=corpus_shape,
        budget=budget,
        leaf_viable_envelope=leaf_viable_envelope,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = response.usage
    cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000

    # Parse JSON
    try:
        plan = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        if start >= 0 and end > start:
            plan = json.loads(raw_text[start:end])
        else:
            plan = {"error": "Failed to parse", "raw": raw_text[:500]}

    # Save rules
    rules = plan.get("rules_of_engagement", "(not found)")
    rules_path = OUT_DIR / "02_rules_of_engagement.md"
    with open(rules_path, "w") as f:
        f.write("# Planner Rules of Engagement\n\n")
        f.write(f"Cost: ${cost:.4f}\n")
        f.write(f"Input tokens: {usage.input_tokens}\n")
        f.write(f"Output tokens: {usage.output_tokens}\n\n")
        f.write("---\n\n")
        f.write(rules)

    # Save scopes
    scopes = plan.get("initial_scopes", [])
    budget_alloc = plan.get("budget_allocation", {})
    scopes_path = OUT_DIR / "03_initial_scopes.md"
    with open(scopes_path, "w") as f:
        f.write("# Planner Initial Scopes\n\n")
        f.write(f"Depth policy: {plan.get('depth_policy', '(not found)')}\n\n")
        f.write("## Budget Allocation\n\n")
        if budget_alloc:
            f.write(f"| Category | Amount |\n|---|---|\n")
            for k, v in budget_alloc.items():
                if k != "reasoning" and isinstance(v, (int, float)):
                    f.write(f"| {k} | ${v:.2f} |\n")
            f.write(f"\n**Reasoning:** {budget_alloc.get('reasoning', '?')}\n\n")
        f.write("---\n\n")
        for i, scope in enumerate(scopes, 1):
            f.write(f"## Scope {i}: {scope.get('name', '?')}\n\n")
            scope_level = scope.get('scope_level', '?')
            scope_level_reasoning = scope.get('scope_level_reasoning', '')
            f.write(f"**Scope level:** {scope_level}")
            if scope_level_reasoning:
                f.write(f" — {scope_level_reasoning}")
            f.write("\n\n")
            f.write(f"**Description:** {scope.get('description', '?')}\n\n")
            f.write(f"**Charter rationale:** {scope.get('charter_rationale', '?')}\n\n")
            f.write(f"**Budget:** ${scope.get('budget', 0):.2f}\n\n")
            f.write(f"**Success criteria:** {scope.get('success_criteria', '?')}\n\n")
            f.write("---\n\n")

    # Save full JSON
    json_path = OUT_DIR / "03_full_plan.json"
    with open(json_path, "w") as f:
        json.dump(plan, f, indent=2)

    print(f"\nOperational plan generated. Cost: ${cost:.4f}")
    print(f"Rules length: {len(rules)} chars")
    print(f"Number of scopes: {len(scopes)}")
    total_scope_budget = sum(s.get("budget", 0) for s in scopes)
    print(f"Total scope budget: ${total_scope_budget:.2f}")
    if budget_alloc:
        inv = budget_alloc.get("investigation_total", 0)
        print(f"Investigation total: ${inv:.2f}")
        print(f"Budget allocation: {json.dumps({k:v for k,v in budget_alloc.items() if k != 'reasoning'}, indent=2)}")
    print(f"Saved to: {rules_path}, {scopes_path}, {json_path}")

    if scopes:
        print(f"\nScope names:")
        for s in scopes:
            print(f"  - {s.get('name', '?')}: ${s.get('budget', 0):.2f}")

    return plan


async def main():
    print("Phase F Milestone 1 — Unit Tests")
    print("================================")
    print("Testing Genesis charter and Planner operational plan")
    print(f"Output directory: {OUT_DIR}")

    # Test 1: Genesis charter
    charter = await test_genesis_charter()

    # Tests 2+3: Planner rules and scopes (using charter from test 1)
    plan = await test_planner_rules_and_scopes(charter)

    print("\n" + "="*60)
    print("ALL UNIT TESTS COMPLETE")
    print("="*60)
    print(f"\nReview outputs in: {OUT_DIR}/")
    print("  01_charter.md — Genesis charter (CEO directive voice)")
    print("  02_rules_of_engagement.md — Planner rules")
    print("  03_initial_scopes.md — Planner initial scopes")
    print("  03_full_plan.json — Full planner output")


if __name__ == "__main__":
    asyncio.run(main())
