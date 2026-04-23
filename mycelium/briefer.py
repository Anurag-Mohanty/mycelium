"""Briefer — generates common knowledge baseline for novelty calibration.

Runs after Genesis, before Planner. Reads Genesis output + survey stats,
produces a Briefing with 10-15 specific claims a domain practitioner would
already know. Workers compare their findings against this to distinguish
novel from confirmatory observations.

Cost target: under $0.10 per briefing.
"""

import json
import random
import anthropic
from .schemas import Briefing


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


async def generate_briefing(genesis_result: dict, catalog_records: list[dict],
                             survey_results: dict = None,
                             source_name: str = "") -> Briefing:
    """Generate a common knowledge briefing from Genesis + survey data.

    Args:
        genesis_result: Output from Genesis (corpus_summary, lenses, etc.)
        catalog_records: Raw records from fetch_bulk_metadata
        survey_results: AnalyticalSurvey output (optional)
        source_name: Human-readable data source name

    Returns:
        Briefing dataclass with common_knowledge populated
    """
    # Build corpus sample — small enough for prompt context
    sample_size = min(50, len(catalog_records))
    sample = random.sample(catalog_records, sample_size) if catalog_records else []
    lightweight_sample = []
    for rec in sample:
        light = {k: v for k, v in rec.items()
                 if k not in ("risk_factors_text", "abstract", "dependencies",
                              "dev_dependencies", "description", "keywords",
                              "latest_versions", "repository", "homepage")
                 or (isinstance(v, str) and len(v) < 100)}
        lightweight_sample.append(light)

    corpus_metadata = json.dumps({
        "source": source_name or "unknown",
        "corpus_summary": genesis_result.get("corpus_summary", ""),
        "total_records": f"{len(catalog_records)} cataloged (sample of {sample_size} shown)",
        "records": lightweight_sample,
    }, indent=2)

    # Build survey findings summary
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
                lines.append(f"  - [{c.get('severity', '?')}] "
                             f"{c.get('name', c.get('description', '?'))}")
        survey_findings = "\n".join(lines)

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

    briefing_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    return Briefing(
        common_knowledge=briefing_text,
        relevant_data_sources=[source_name] if source_name else [],
        cost=cost,
        token_usage=usage,
    )
