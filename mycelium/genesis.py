"""Genesis Node — surveys corpus shape and generates attention lenses.

Runs ONCE before exploration. Looks at structural metadata and generates
the lenses that every node will use. Optionally accepts user hints to
influence lens weighting (not selection).
"""

import json
import random
import anthropic
from .prompts import GENESIS_PROMPT


async def run_genesis(data_source, hints: list[str] = None,
                      catalog_records: list[dict] = None) -> dict:
    """Survey the corpus shape and generate attention lenses.

    If catalog_records provided, samples from those (wider coverage from
    the 2000 already fetched). Otherwise falls back to survey() which
    fetches ~200 fresh. Either way, genesis sees RAW records, not stats.

    Args:
        data_source: A DataSource instance to survey
        hints: Optional user context to influence lens generation
        catalog_records: Raw records from fetch_bulk_metadata (if available)

    Returns:
        dict with corpus_summary, lenses, suggested_entry_points,
        natural_structure, and token usage
    """
    if catalog_records and len(catalog_records) > 0:
        # Sample from the already-fetched catalog — wider coverage
        # Strip heavy text fields — genesis needs corpus SHAPE, not full content
        sample_size = min(200, len(catalog_records))
        sample = random.sample(catalog_records, sample_size)
        lightweight_sample = []
        for rec in sample:
            light = {k: v for k, v in rec.items()
                     if k not in ("risk_factors_text", "abstract", "dependencies",
                                  "dev_dependencies", "description")
                     or (isinstance(v, str) and len(v) < 200)}
            lightweight_sample.append(light)
        survey = {
            "source": "catalog_sample",
            "total_packages": f"{len(catalog_records)} cataloged (sample of {sample_size} shown)",
            "scope": "broad ecosystem survey",
            "packages": lightweight_sample,
        }
    else:
        survey = await data_source.survey({})

    corpus_metadata = json.dumps(survey, indent=2)

    hints_str = "\n".join(f"- {h}" for h in hints) if hints else \
        "No hints provided — explore with fresh eyes"

    prompt = GENESIS_PROMPT.format(
        corpus_metadata=corpus_metadata,
        hints=hints_str,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw_text[start:end])
        else:
            raise ValueError(f"Genesis returned invalid JSON: {raw_text[:200]}")

    result["token_usage"] = usage
    result["cost"] = cost
    return result
