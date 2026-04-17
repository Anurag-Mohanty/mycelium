"""Genesis Node — surveys corpus shape and generates attention lenses.

Runs ONCE before exploration. Looks at structural metadata and generates
the lenses that every node will use. Optionally accepts user hints to
influence lens weighting (not selection).
"""

import json
import anthropic
from .prompts import GENESIS_PROMPT


async def run_genesis(data_source, hints: list[str] = None) -> dict:
    """Survey the corpus shape and generate attention lenses.

    Genesis sees RAW survey data (200 packages), not pre-digested catalog stats.
    This keeps it curious — it designs broad exploration from a sample,
    rather than treating statistics as conclusions.

    Args:
        data_source: A DataSource instance to survey
        hints: Optional user context to influence lens generation

    Returns:
        dict with corpus_summary, lenses, suggested_entry_points,
        natural_structure, and token usage
    """
    # If we have catalog stats, use those (richer, from thousands of records)
    # Otherwise fall back to the data source's survey method
    # Genesis sees raw survey data for segment design.
    # If catalog already fetched bulk records, use those (bigger sample).
    # Otherwise fall back to survey() which fetches ~200.
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
