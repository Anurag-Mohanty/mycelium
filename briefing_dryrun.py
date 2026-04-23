#!/usr/bin/env python3
"""Briefing dry-run — generate corpus briefings via EQUIP resolution.

Takes a natural-language query, resolves it through the same _resolve_data_source()
function run.py uses, attaches the connector (built-in or GenericAPISource), runs
a corpus survey, and generates a common knowledge briefing.

Usage:
    python3 briefing_dryrun.py "npm package registry"
    python3 briefing_dryrun.py "SEC EDGAR 10-K filings"
    python3 briefing_dryrun.py "FDA adverse event reports"

    # Run all test queries:
    python3 briefing_dryrun.py --all
"""

import asyncio
import json
import os
import random
import sys
import anthropic

# Load .env if present
from pathlib import Path
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


BRIEFING_PROMPT = """\
You have surveyed the structural metadata of an information space. Here is what \
you can see about its shape:

CORPUS METADATA (sample of records):
{corpus_metadata}

STATISTICAL SURVEY FINDINGS (from programmatic analysis):
{survey_findings}

YOUR TASK: Write a COMMON KNOWLEDGE BRIEFING about this corpus.

This briefing captures what a knowledgeable reader of this domain would already \
expect to be true BEFORE any investigation begins. It serves as a baseline — \
anything an investigator discovers that is NOT in this briefing is potentially \
novel. Anything that IS in this briefing is confirmatory (expected, not surprising).

Write 10-15 specific, concrete claims. Each claim should be:
- Something a domain practitioner would nod at ("yes, everyone knows that")
- Specific enough to be falsifiable ("X tends to have property Y" not "some things vary")
- About the DOMAIN, not the dataset shape (not "there are 26,000 records" but \
"10-K filings follow annual reporting cycles with seasonal clustering")
- Where widely known, name specific entities and quantities (e.g., "lodash, express, \
and react each serve hundreds of millions of monthly downloads" not "some packages \
are popular")

Do NOT include:
- Speculation or hypotheses requiring investigation to confirm
- Rare phenomena or edge cases
- Claims about dataset quality or collection methodology
- Generic truisms that apply to any corpus ("some entities are larger than others")

Format as a numbered list of claims. Target roughly one page. Each claim should \
be one sentence, optionally followed by a brief elaboration.

Respond with ONLY the numbered list of claims, no preamble or conclusion.
"""

ALL_QUERIES = [
    "npm package registry",
    "PyPI Python package index",
    "SEC EDGAR 10-K filings",
    "US Federal Register regulations",
    "USPTO patent filings",
    "FDA adverse event reports",
]


async def resolve_source(query: str) -> dict:
    """Resolve a query through EQUIP — reuses run.py's _resolve_data_source."""
    # Import the resolver from run.py
    sys.path.insert(0, str(Path(__file__).parent))
    from run import _resolve_data_source, create_data_source
    from mycelium.data_sources.generic_api import GenericAPISource

    print(f"\n{'='*60}")
    print(f"QUERY: \"{query}\"")
    print(f"{'='*60}")

    resolved = await _resolve_data_source(query)

    result = {
        "query": query,
        "resolved": resolved,
        "connector_type": None,
        "data_source": None,
        "api_config": None,
    }

    if not resolved.get("is_exploration", True):
        result["connector_type"] = "not_exploration"
        print(f"  Not an exploration request: {resolved.get('message', '')}")
        return result

    if resolved.get("connector"):
        connector_name = resolved["connector"]
        result["connector_type"] = f"built-in:{connector_name}"
        print(f"  Resolved to built-in connector: {connector_name}")
        try:
            result["data_source"] = create_data_source(connector_name)
        except SystemExit:
            # create_data_source calls sys.exit on unknown name
            result["connector_type"] = f"built-in:{connector_name} (unknown)"
            print(f"  WARNING: connector name '{connector_name}' not recognized by create_data_source")
            return result
        return result

    if resolved.get("api_config"):
        api_config = resolved["api_config"]
        result["connector_type"] = "generic_api"
        result["api_config"] = api_config
        result["data_source"] = GenericAPISource(api_config)
        print(f"  Dynamic connector: {api_config.get('source_name', '?')}")
        print(f"  Base URL: {api_config.get('base_url', '?')}")
        print(f"  Endpoint: {api_config.get('search_endpoint', '?')}")
        return result

    result["connector_type"] = "unavailable"
    print(f"  Unavailable: {resolved.get('message', 'no API found')}")
    return result


async def generate_briefing(data_source, source_label: str) -> dict:
    """Run survey + generate briefing for a resolved data source."""

    # Fetch records
    print(f"  Fetching records...")
    if hasattr(data_source, 'fetch_bulk_metadata'):
        bulk_records = await data_source.fetch_bulk_metadata(
            max_records=2000, progress_callback=lambda p: print(
                f"\r  {p.get('fetched', 0)}/{p.get('total_estimated', '?')} records",
                end="", flush=True))
    else:
        # Fallback for connectors without bulk fetch
        bulk_records = await data_source.fetch({}, max_results=200)
    print()

    if not bulk_records:
        print(f"  No records returned. Skipping briefing.")
        return {"records": 0, "briefing": None, "cost": 0}

    print(f"  {len(bulk_records)} records. Running survey...")

    # Run statistical survey (skip if too few records)
    survey_findings = "No statistical survey (insufficient records)."
    if len(bulk_records) >= 20:
        try:
            from mycelium.survey import ProgrammaticSurvey
            survey_engine = ProgrammaticSurvey()
            catalog_stats = survey_engine.analyze(bulk_records)
            lines = []
            lines.append(f"Records analyzed: {catalog_stats.get('record_count', '?')}")
            lines.append(f"Techniques applied: {', '.join(catalog_stats.get('techniques_applied', []))}")
            lines.append(f"Outliers found: {len(catalog_stats.get('outliers', []))}")
            lines.append(f"Concentrations: {len(catalog_stats.get('concentrations', []))}")
            clusters = catalog_stats.get("anomaly_clusters", [])
            if clusters:
                lines.append(f"Multi-flagged anomaly clusters: {len(clusters)}")
                for c in clusters[:10]:
                    lines.append(f"  - [{c.get('severity', '?')}] {c.get('name', c.get('description', '?'))}")
            survey_findings = "\n".join(lines)
            print(f"  Survey complete: {catalog_stats['record_count']} records")
        except Exception as e:
            print(f"  Survey failed: {e}")
            survey_findings = f"Survey failed: {e}"

    # Build corpus sample
    sample_size = min(50, len(bulk_records))
    sample = random.sample(bulk_records, sample_size)
    lightweight_sample = []
    for rec in sample:
        light = {k: v for k, v in rec.items()
                 if k not in ("risk_factors_text", "abstract", "dependencies",
                              "dev_dependencies", "description", "keywords",
                              "latest_versions", "repository", "homepage")
                 or (isinstance(v, str) and len(v) < 100)}
        lightweight_sample.append(light)

    corpus_metadata = json.dumps({
        "source": source_label,
        "total_records": f"{len(bulk_records)} cataloged (sample of {sample_size} shown)",
        "records": lightweight_sample,
    }, indent=2)

    # Generate briefing
    prompt = BRIEFING_PROMPT.format(
        corpus_metadata=corpus_metadata,
        survey_findings=survey_findings,
    )

    print(f"  Generating briefing (prompt: {len(prompt)} chars)...")
    if len(prompt) > 150000:
        print(f"  WARNING: prompt too large ({len(prompt)} chars), truncating sample")
        lightweight_sample = lightweight_sample[:20]
        corpus_metadata = json.dumps({
            "source": source_label,
            "total_records": f"{len(bulk_records)} cataloged (sample of {len(lightweight_sample)} shown)",
            "records": lightweight_sample,
        }, indent=2)
        prompt = BRIEFING_PROMPT.format(
            corpus_metadata=corpus_metadata,
            survey_findings=survey_findings,
        )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    briefing = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000

    print(f"  Cost: ${cost:.3f} ({input_tokens} in, {output_tokens} out)")

    if cost > 0.30:
        print(f"  WARNING: cost exceeds $0.30 threshold")

    return {
        "records": len(bulk_records),
        "briefing": briefing,
        "cost": cost,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def run_single(query: str):
    """Run EQUIP resolution + briefing for a single query."""
    result = await resolve_source(query)

    if not result["data_source"]:
        return result

    briefing_result = await generate_briefing(
        result["data_source"],
        result.get("api_config", {}).get("source_name", query) if result["api_config"] else query,
    )
    result.update(briefing_result)

    # Save briefing
    if briefing_result.get("briefing"):
        safe_name = query.lower().replace(" ", "_")[:30]
        out_path = f"/tmp/briefing_{safe_name}.md"
        with open(out_path, "w") as f:
            f.write(f"# Common Knowledge Briefing: {query}\n\n")
            f.write(f"*Connector: {result['connector_type']} | "
                    f"Records: {briefing_result['records']} | "
                    f"Cost: ${briefing_result['cost']:.3f}*\n\n")
            f.write(briefing_result["briefing"])
        print(f"  Saved to {out_path}")

    # Print briefing
    if briefing_result.get("briefing"):
        print(f"\n{briefing_result['briefing']}")

    await result["data_source"].close()
    return result


async def run_all():
    """Run all test queries sequentially."""
    results = []
    total_cost = 0

    for query in ALL_QUERIES:
        result = await run_single(query)
        results.append(result)
        total_cost += result.get("cost", 0)

        if total_cost > 2.00:
            print(f"\n  STOPPING: total cost ${total_cost:.2f} exceeds $2.00 budget")
            break

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"{'Query':40s} {'Connector':20s} {'Records':>8s} {'Cost':>8s}")
    print("-" * 80)
    for r in results:
        q = r.get("query", "?")[:38]
        ct = r.get("connector_type", "?")[:18]
        rec = str(r.get("records", "-"))
        cost = f"${r.get('cost', 0):.3f}" if r.get("cost") else "-"
        print(f"{q:40s} {ct:20s} {rec:>8s} {cost:>8s}")
    print(f"\nTotal cost: ${total_cost:.3f}")


async def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 briefing_dryrun.py \"npm package registry\"")
        print("  python3 briefing_dryrun.py --all")
        sys.exit(1)

    if sys.argv[1] == "--all":
        await run_all()
    else:
        query = " ".join(sys.argv[1:])
        await run_single(query)


if __name__ == "__main__":
    asyncio.run(main())
