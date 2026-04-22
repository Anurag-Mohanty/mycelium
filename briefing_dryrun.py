#!/usr/bin/env python3
"""Briefing dry-run — generate a corpus briefing without running exploration.

Usage:
    python3 briefing_dryrun.py npm
    python3 briefing_dryrun.py sec
    python3 briefing_dryrun.py federal
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

Do NOT include:
- Speculation or hypotheses requiring investigation to confirm
- Rare phenomena or edge cases
- Claims about dataset quality or collection methodology
- Generic truisms that apply to any corpus ("some entities are larger than others")

Format as a numbered list of claims. Target roughly one page. Each claim should \
be one sentence, optionally followed by a brief elaboration.

Respond with ONLY the numbered list of claims, no preamble or conclusion.
"""


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 briefing_dryrun.py <npm|sec|federal>")
        sys.exit(1)

    source_name = sys.argv[1].lower()

    # Load data source
    if source_name == "npm":
        from mycelium.data_sources.npm_registry import NpmRegistrySource
        data_source = NpmRegistrySource()
    elif source_name == "sec":
        from mycelium.data_sources.sec_edgar import SecEdgarSource
        data_source = SecEdgarSource()
    elif source_name == "federal":
        from mycelium.data_sources.federal_register import FederalRegisterSource
        data_source = FederalRegisterSource()
    else:
        print(f"Unknown source: {source_name}")
        sys.exit(1)

    print(f"Loading {source_name} catalog data...")
    bulk_records = await data_source.fetch_bulk_metadata(
        max_records=2000, progress_callback=lambda p: print(
            f"\r  {p.get('fetched', 0)}/{p.get('total_estimated', '?')} records",
            end="", flush=True))
    print()

    if not bulk_records:
        print("No records loaded. Exiting.")
        sys.exit(1)

    print(f"Loaded {len(bulk_records)} records. Running survey...")

    # Run statistical survey
    from mycelium.survey import ProgrammaticSurvey
    survey_engine = ProgrammaticSurvey()
    catalog_stats = survey_engine.analyze(bulk_records)
    print(f"Survey complete: {catalog_stats['record_count']} records, "
          f"{len(catalog_stats.get('anomaly_clusters', []))} clusters")

    # Build corpus sample — smaller than Genesis to keep prompt under context
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
        "source": f"{source_name}_catalog_sample",
        "total_records": f"{len(bulk_records)} cataloged (sample of {sample_size} shown)",
        "records": lightweight_sample,
    }, indent=2)

    # Build survey findings summary
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

    # Generate briefing
    prompt = BRIEFING_PROMPT.format(
        corpus_metadata=corpus_metadata,
        survey_findings=survey_findings,
    )

    print(f"\nGenerating briefing (prompt: {len(prompt)} chars)...")
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

    print(f"Cost: ${cost:.3f} ({input_tokens} in, {output_tokens} out)")

    # Save
    out_path = f"/tmp/briefing_{source_name}.md"
    with open(out_path, "w") as f:
        f.write(f"# Common Knowledge Briefing: {source_name.upper()}\n\n")
        f.write(f"*Cost: ${cost:.3f} | {input_tokens} input tokens, {output_tokens} output tokens*\n\n")
        f.write(briefing)

    print(f"Saved to {out_path}")
    print(f"\n{'='*60}\n")
    print(briefing)

    await data_source.close()


if __name__ == "__main__":
    asyncio.run(main())
