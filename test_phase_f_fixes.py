"""Unit tests for Phase F fixes: date context and active charter reference.

Fix 1: Charter should NOT frame current-year timestamps as anomalous
Fix 2: Workers should actively check observations against charter's forbidden list
"""

import asyncio
import json
import datetime
import anthropic
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path("output/phase_f_fix_tests")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --- Fix 1: Date context in charter ---

async def test_charter_date_context():
    """Verify charter doesn't flag current-year timestamps as impossible."""
    print("\n" + "="*60)
    print("FIX 1: Charter Date Context")
    print("="*60)

    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import CHARTER_PROMPT

    # Minimal corpus metadata with 2026 timestamps
    corpus_metadata = json.dumps({
        "source": "npm_registry",
        "total_packages": "100726 cataloged (sample of 5 shown)",
        "packages": [
            {"name": "react", "last_modified": "2026-04-17", "monthly_downloads": 502719511},
            {"name": "lodash", "last_modified": "2026-04-18", "monthly_downloads": 580261537},
            {"name": "express", "last_modified": "2026-04-16", "monthly_downloads": 312000000},
            {"name": "typescript", "last_modified": "2026-04-16", "monthly_downloads": 721000000},
            {"name": "old-pkg", "last_modified": "2019-03-01", "monthly_downloads": 50},
        ],
    }, indent=2)

    survey_findings = (
        "Records analyzed: 100726\n"
        "Techniques applied: basic_stats, isolation_forest, temporal\n"
        "Outliers found: 50\n"
        "Multi-flagged anomaly clusters: 25"
    )

    briefing = (
        "1. npm follows a power law distribution.\n"
        "2. Most packages have single maintainers.\n"
        "3. lodash and react dominate downloads."
    )

    prompt = CHARTER_PROMPT.format(
        current_date=datetime.date.today().isoformat(),
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
    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

    # Check: does charter call 2026 timestamps "impossible" or "anomalous"?
    charter_lower = charter.lower()
    bad_phrases = [
        "impossible timestamp",
        "impossible date",
        "future-dated",
        "future date",
        "2026 anomal",
        "timestamps in 2026",
        "dates in 2026",
        "creation dates in 2026",
    ]
    found_bad = [p for p in bad_phrases if p in charter_lower]

    print(f"\nCharter generated. Cost: ${cost:.4f}")
    print(f"Length: {len(charter.split())} words")

    if found_bad:
        print(f"\n  FAIL: Charter still flags 2026 timestamps as anomalous:")
        for p in found_bad:
            idx = charter_lower.find(p)
            snippet = charter[max(0, idx-30):idx+len(p)+30]
            print(f"    '{p}' in: ...{snippet}...")
    else:
        print(f"\n  PASS: Charter does NOT flag 2026 timestamps as anomalous")

    # Save
    out_path = OUT_DIR / "fix1_charter.md"
    with open(out_path, "w") as f:
        f.write(f"# Fix 1: Charter Date Context\n\n")
        f.write(f"Cost: ${cost:.4f}\n")
        f.write(f"Bad phrases found: {found_bad}\n")
        f.write(f"Result: {'FAIL' if found_bad else 'PASS'}\n\n---\n\n")
        f.write(charter)

    print(f"Saved to: {out_path}")
    return charter, not found_bad


# --- Fix 2: Active charter reference ---

async def test_active_charter_reference():
    """Test that workers suppress/reframe observations matching charter's forbidden list."""
    print("\n" + "="*60)
    print("FIX 2: Active Charter Reference")
    print("="*60)

    from mycelium import prompts
    prompts.set_version("v2")
    from mycelium.prompts import NODE_REASONING_PROMPT

    # A charter that explicitly forbids "lodash has one maintainer" findings
    test_charter = """**ORGANIZATIONAL CHARTER**

Team, we are investigating the npm ecosystem — 100,726 packages.

**What Is Already Known — Don't Waste Time Here**

The following patterns are well understood by any senior engineer:
- lodash is maintained by a single individual (jdalton) and receives ~580M monthly downloads
- Most npm packages have single maintainers
- Popular packages like react, express, typescript dominate download counts
- The ecosystem follows a power law distribution
- MIT is the dominant license

Do NOT bring me observations that restate these known patterns. If you find yourself
writing "lodash has 580M downloads and one maintainer" you have wasted your time.

**What Impresses Us**

Findings that reveal hidden structure: specific dependency chains creating unmapped
chokepoints, adoption dynamics that contradict surface metrics, maintainer networks
whose actual ecosystem impact differs from their public profile. Named entities,
exact numbers, traceable evidence required."""

    test_rules = """RULES OF ENGAGEMENT
1. NOVELTY REQUIREMENT: Reject findings matching the charter's known-pattern list.
2. EVIDENCE STANDARDS: Named packages, exact numbers, traceable methodology.
3. BUDGET: Spend aggressively on genuine anomalies, abandon known patterns immediately."""

    # Build the briefing_context as the worker would
    briefing_context = (
        "## ORGANIZATIONAL CHARTER (read this — it defines quality standards)\n\n"
        + test_charter + "\n\n"
        "## RULES OF ENGAGEMENT (operational policies for all workers)\n\n"
        + test_rules + "\n\n"
    )

    # Simulate a worker with data that includes lodash
    # The worker should NOT produce "lodash has 1 maintainer" as an observation
    test_data = json.dumps([
        {"name": "lodash", "monthly_downloads": 580261537, "maintainer_count": 1,
         "maintainers": "jdalton", "dependency_count": 0, "version_count": 114,
         "created": "2012-04-23", "last_modified": "2026-04-18", "license": "MIT"},
        {"name": "chalk", "monthly_downloads": 312000000, "maintainer_count": 1,
         "maintainers": "sindresorhus", "dependency_count": 0, "version_count": 56,
         "created": "2013-07-18", "last_modified": "2025-01-15", "license": "MIT"},
        {"name": "mime-db", "monthly_downloads": 851000000, "maintainer_count": 1,
         "maintainers": "dougwilson", "dependency_count": 0, "version_count": 228,
         "created": "2014-01-20", "last_modified": "2025-05-14", "license": "MIT"},
    ], indent=2)

    prompt = NODE_REASONING_PROMPT.format(
        current_date=datetime.date.today().isoformat(),
        purpose="Investigate maintainer concentration patterns in critical packages",
        parent_context="Planner assigned this scope to find hidden maintainer risks",
        briefing_context=briefing_context,
        scope_description="Analyze maintainer patterns in high-download packages",
        lenses="concentration_risk, single_points_of_failure, hidden_dependencies",
        filter_schema="(not relevant for this test)",
        budget_remaining=0.25,
        total_budget=2.0,
        budget_pct=12.5,
        budget_stage="Early investigation — explore broadly.",
        capacity_context="",
        segment_context="",
        parent_pool_remaining=1.50,
        phase_remaining=1.50,
        current_depth=0,
        max_depth=6,
        leaf_viable_envelope=0.12,
        depth_guidance="You may spawn children if threads warrant it.",
        doc_count=3,
        fetched_data=test_data,
        force_resolve="",
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": 5000},
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract thinking and output
    thinking = ""
    output_text = ""
    for block in response.content:
        if block.type == "thinking":
            thinking = block.thinking
        elif block.type == "text":
            output_text = block.text

    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000

    # Parse observations
    try:
        result = json.loads(output_text)
    except json.JSONDecodeError:
        start = output_text.find("{")
        end = output_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(output_text[start:end])
        else:
            result = {"observations": [], "error": "parse_failed"}

    observations = result.get("observations", [])

    # Check: did the worker produce a "lodash has 1 maintainer" observation?
    lodash_known_obs = []
    lodash_reframed_obs = []
    suppressed = False

    for obs in observations:
        raw = obs.get("raw_evidence", "").lower()
        hyp = obs.get("local_hypothesis", "").lower()
        sig = obs.get("signal_strength", "")

        # Known pattern: just restating lodash has 1 maintainer with high downloads
        if "lodash" in raw and ("1 maintainer" in raw or "jdalton" in raw or "single maintainer" in raw):
            if sig == "confirmatory" or "known" in hyp or "already known" in hyp:
                lodash_known_obs.append(obs)
            else:
                lodash_reframed_obs.append(obs)

    # Check thinking for charter awareness
    charter_aware = any(phrase in thinking.lower() for phrase in [
        "charter", "already known", "don't bring me", "forbidden",
        "suppress", "reframe", "compliance", "known pattern",
    ])

    print(f"\nCost: ${cost:.4f}")
    print(f"Observations produced: {len(observations)}")
    print(f"Charter-aware thinking: {charter_aware}")
    print(f"Lodash known-pattern observations: {len(lodash_known_obs)}")
    print(f"Lodash reframed observations: {len(lodash_reframed_obs)}")

    # Results
    if lodash_known_obs:
        print(f"\n  FAIL: Worker produced {len(lodash_known_obs)} known-pattern lodash observations:")
        for obs in lodash_known_obs:
            print(f"    - {obs.get('raw_evidence', '')[:100]}")
    elif lodash_reframed_obs:
        print(f"\n  PARTIAL: Worker reframed lodash (good!) but still included it:")
        for obs in lodash_reframed_obs:
            print(f"    - {obs.get('raw_evidence', '')[:100]}")
    else:
        print(f"\n  PASS: No lodash known-pattern observations produced")

    if not charter_aware:
        print(f"  WARNING: Thinking doesn't show explicit charter awareness")
    else:
        print(f"  PASS: Thinking shows charter awareness")

    # Save full output
    out_path = OUT_DIR / "fix2_charter_reference.md"
    with open(out_path, "w") as f:
        f.write(f"# Fix 2: Active Charter Reference\n\n")
        f.write(f"Cost: ${cost:.4f}\n")
        f.write(f"Observations: {len(observations)}\n")
        f.write(f"Charter-aware thinking: {charter_aware}\n")
        f.write(f"Lodash known-pattern obs: {len(lodash_known_obs)}\n")
        f.write(f"Lodash reframed obs: {len(lodash_reframed_obs)}\n\n")
        f.write(f"## Thinking\n\n{thinking[:3000]}\n\n")
        f.write(f"## Observations\n\n")
        for i, obs in enumerate(observations, 1):
            f.write(f"### Obs {i}\n")
            f.write(f"**Signal:** {obs.get('signal_strength', '?')}\n")
            f.write(f"**Evidence:** {obs.get('raw_evidence', '?')[:200]}\n")
            f.write(f"**Hypothesis:** {obs.get('local_hypothesis', '?')[:200]}\n\n")

    print(f"Saved to: {out_path}")

    passed = not lodash_known_obs and charter_aware
    return passed


async def main():
    print("Phase F Fix Tests")
    print("="*60)

    # Fix 1
    charter, fix1_pass = await test_charter_date_context()

    # Fix 2
    fix2_pass = await test_active_charter_reference()

    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"  Fix 1 (date context):          {'PASS' if fix1_pass else 'FAIL'}")
    print(f"  Fix 2 (active charter ref):    {'PASS' if fix2_pass else 'FAIL'}")
    print(f"\nOutputs in: {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
