"""Validator — node-based skeptical reviewer with corpus access.

Runs four parallel operations on each finding:
  1. Factual Re-Query — extract claims, fetch from corpus, confirm/refute
  2. Triangulation — count independent observations supporting the finding
  3. Falsification — actively try to disprove using corpus evidence
  4. Surprise Scoring — compare against common-knowledge briefing

Integration synthesizes the four signals into a final verdict.
"""

import asyncio
import json
import anthropic
from .schemas import ValidationResult


# --- Operation Prompts ---

FACTUAL_REQUERY_PROMPT = """\
You are a fact-checker with direct access to the full corpus database.

FINDING:
{finding_desc}

EVIDENCE CITED:
{evidence}

CORPUS RECORDS (entity lookups):
{corpus_records}

CORPUS QUERY RESULTS (pattern verification):
{query_results}

This finding contains two types of claims. Handle each differently:

SPECIFIC CLAIMS (named entities, exact values): Look for the entity in the \
corpus records above. If found, compare the claimed value to the actual value. \
CONFIRMED if values match. REFUTED if they contradict. UNVERIFIABLE only if \
the entity is genuinely absent from the corpus.

PATTERN CLAIMS (statistical assertions like "X tends to Y" or "most Z have W"): \
The query results above show counts from the full corpus. Compare the finding's \
pattern assertion to the actual counts. If the pattern holds, CONFIRMED. If the \
counts contradict the pattern, REFUTED.

UNVERIFIABLE should be RARE. If a claim can be rephrased as a countable query \
against the corpus and the query results are present above, it is verifiable.

Return JSON:
{{
    "claims": [
        {{
            "claim": "the specific factual claim",
            "claim_type": "specific | pattern",
            "corpus_evidence": "what the corpus actually shows — cite record or count",
            "verdict": "CONFIRMED | REFUTED | UNVERIFIABLE"
        }}
    ],
    "summary_verdict": "CONFIRMED | PARTIALLY_CONFIRMED | REFUTED | UNVERIFIABLE",
    "confirmed_count": 0,
    "refuted_count": 0,
    "unverifiable_count": 0
}}

Respond ONLY with valid JSON."""


TRIANGULATION_PROMPT = """\
You are assessing how many independent lines of evidence support this finding.

FINDING:
{finding_desc}

ALL OBSERVATIONS FROM THE EXPLORATION (each labeled with worker position and partition):
{all_observations}

Count how many INDEPENDENT observations support this finding. Independence means:
- The observations came from DIFFERENT partitions (different data slices)
- They were produced by DIFFERENT workers
- They describe the SAME pattern or entity, not just similar topics

Do NOT count as independent:
- The same observation cited multiple times
- Observations from the same partition (same worker examining the same data)
- Observations about different topics that happen to use similar language

Return JSON:
{{
    "supporting_observations": [
        {{
            "observation_summary": "what this observation says",
            "worker_partition": "which data slice this came from",
            "independence_reasoning": "why this is an independent line of evidence"
        }}
    ],
    "independent_count": 0,
    "score": "STRONG | MODERATE | WEAK",
    "reasoning": "why this score — STRONG requires 3+ independent observations from different partitions"
}}

Respond ONLY with valid JSON."""


FALSIFICATION_PROMPT = """\
Your goal is to kill this finding using evidence from the corpus.

FINDING TO DISPROVE:
{finding_desc}

EVIDENCE THE FINDING CITES:
{evidence}

CORPUS RECORDS (fetched to search for counter-evidence):
{corpus_records}

CORPUS QUERY RESULTS (pattern tests):
{query_results}

Procedure:
1. Identify the finding's central claim. Write it as a falsifiable statement.
2. Search the corpus records and query results for data that contradicts \
   this statement.
3. If you find contradicting records, cite them with specific identifiers \
   and explain how they contradict. The finding is KILLED.
4. If you find records that complicate but don't refute the finding, cite \
   them. The finding is WEAKENED.
5. If you cannot find contradicting records after active search, the finding \
   SURVIVES. State what you searched for and why the corpus doesn't contain \
   counter-evidence.

For claim-shaped findings ("X has Y downloads"), counter-evidence is a record \
showing X has a different value.

For pattern-shaped findings ("packages with P tend to have Q"), counter-evidence \
is records or counts showing the pattern doesn't hold. Counter-examples must \
be substantial, not isolated.

CRITICAL RULE: You must NOT return KILLED based on reasoning alone. If your \
reasoning produces a counter-argument but you cannot point to a specific corpus \
record or query result that supports it, return SURVIVED with a note in \
"hypothetical_objections" explaining what you thought of but couldn't ground \
in evidence.

Return JSON:
{{
    "falsifiable_statement": "the finding's claim rephrased as a testable statement",
    "counter_evidence": [
        {{
            "record_or_query": "specific corpus record ID or query result cited",
            "what_contradicts": "the data point that undermines the finding",
            "how_it_contradicts": "why this contradicts the claim"
        }}
    ],
    "hypothetical_objections": ["counter-arguments generated but NOT grounded in corpus evidence"],
    "verdict": "KILLED | WEAKENED | SURVIVED",
    "kill_reasoning": "if KILLED: cite the specific records that refute the finding",
    "survival_reasoning": "if SURVIVED: what you searched for and why the corpus doesn't contain counter-evidence"
}}

Respond ONLY with valid JSON."""


SURPRISE_SCORING_PROMPT = """\
You will receive a finding and the common-knowledge briefing for this corpus. \
Determine whether the finding restates briefing content, extends it, or \
contradicts it.

FINDING:
{finding_desc}

COMMON-KNOWLEDGE BRIEFING (what a domain practitioner already knows):
{briefing}

Procedure:
1. Read the briefing carefully. It represents what a domain practitioner \
   already knows about this corpus.
2. Read the finding. Identify the finding's specific claim.
3. Search the briefing for content that overlaps with the finding's claim.
4. Classify:

LOW: The briefing already states or directly implies this finding. The finding \
restates common knowledge with at most minor specifics added. Example: if the \
briefing says "the Vue ecosystem is controlled by Evan You" and the finding says \
"yyx990803 controls 8 Vue packages totaling 455M downloads" — that's LOW. The \
numbers add specificity but the structural claim is in the briefing.

MEDIUM: The finding extends briefing content with specific quantification, \
named examples, or scope expansion that wouldn't be obvious from the briefing \
alone.

HIGH: The finding contradicts the briefing or addresses something the briefing \
doesn't cover and a knowledgeable practitioner would not derive from general \
knowledge. If the briefing doesn't mention coordinated launch timing and the \
finding identifies a specific cluster of synchronized releases — that's HIGH.

You MUST quote the relevant briefing passage when assigning LOW or MEDIUM. If \
you cannot find a passage to quote, the rating is HIGH by default.

Return JSON:
{{
    "closest_briefing_content": "QUOTED passage from the briefing most related to this finding, or 'no relevant passage found'",
    "relationship": "contradicts | extends | restates",
    "score": "HIGH | MEDIUM | LOW",
    "reasoning": "why this score — cite what the briefing does or doesn't say"
}}

Respond ONLY with valid JSON."""


INTEGRATION_PROMPT = """\
You are integrating four independent assessments of a finding into a final verdict.

FINDING:
{finding_desc}

OPERATION 1 — FACTUAL RE-QUERY:
{factual_result}

OPERATION 2 — TRIANGULATION:
{triangulation_result}

OPERATION 3 — FALSIFICATION ATTEMPT:
{falsification_result}

OPERATION 4 — SURPRISE SCORING:
{surprise_result}

Integration rules — weight signals by evidence quality, not just labels:

1. Check falsification evidence quality first:
   - If falsification returned KILLED with specific corpus records cited in \
     counter_evidence → the kill is grounded. Weight it heavily.
   - If falsification returned KILLED but counter_evidence is empty or only \
     contains hypothetical_objections → the kill is NOT grounded. Treat as SURVIVED.
   - If triangulation is STRONG and falsification is KILLED, the falsifier must \
     explain why multiple independent observations are wrong. If it doesn't, \
     downgrade the kill to WEAKENED.

2. Apply verdicts:
   - CONFIRMED: factual CONFIRMED or PARTIALLY_CONFIRMED with most claims verified, \
     falsification SURVIVED (or ungrounded KILLED), triangulation STRONG or MODERATE.
   - CONFIRMED_WITH_CAVEATS: factual claims mostly confirmed, falsification SURVIVED, \
     but triangulation WEAK or surprise LOW.
   - WEAKENED: some factual claims UNVERIFIABLE, or falsification WEAKENED with \
     grounded counter-evidence.
   - REFUTED: falsification KILLED with grounded corpus evidence, or factual \
     re-query found REFUTED claims with cited records.
   - INSUFFICIENT_EVIDENCE: factual claims mostly UNVERIFIABLE and no other \
     signal is strong.

3. Surprise scoring (LOW) does not refute a finding — it flags it as restating \
   common knowledge. A confirmed finding with LOW surprise is confirmed but not novel.

Return JSON:
{{
    "verdict": "confirmed | confirmed_with_caveats | weakened | refuted | insufficient_evidence",
    "adjusted_confidence": 0.0,
    "adjusted_tier": 3,
    "reasoning": "one paragraph explaining how the four signals combined — cite the evidence each operation provided",
    "verification_action": "what specific lookup would resolve remaining uncertainty",
    "revised_finding": "reworded finding separating confirmed facts from uncertain interpretation, or null",
    "is_pipeline_issue": false,
    "pipeline_issue_reasoning": ""
}}

Respond ONLY with valid JSON."""


# --- Core Functions ---

async def validate_finding(finding_id: str, finding_type: str, finding: dict,
                           data_source=None, run_dir: str = None,
                           briefing_text: str = "") -> ValidationResult:
    """Validate a finding using four parallel corpus-grounded operations.

    Args:
        finding_id: Unique identifier for this finding
        finding_type: "contradiction", "gap", or "cross_cutting_pattern"
        finding: The finding dict from synthesis output
        data_source: corpus data source (for factual re-query and falsification)
        run_dir: path to run output directory (for loading observations)
        briefing_text: common-knowledge briefing for surprise scoring

    Returns:
        ValidationResult with verdict, four operation outputs, and integration
    """
    # Format the finding
    if finding_type == "contradiction":
        finding_desc = finding.get("what_conflicts", "")
        evidence = json.dumps({
            "side_a": finding.get("side_a", {}),
            "side_b": finding.get("side_b", {}),
            "significance": finding.get("significance", ""),
        }, indent=2)
    else:
        finding_desc = finding.get("pattern", "")
        evidence = json.dumps({
            "evidence_chain": finding.get("evidence_chain", []),
            "confidence": finding.get("confidence", 0),
            "inferred_links": finding.get("inferred_links", []),
        }, indent=2)

    # Extract entity names and pattern claims for corpus queries
    entities = _extract_entities(finding_desc, evidence)

    # Fetch corpus records for entity-level verification
    corpus_records = ""
    query_results = ""
    if data_source:
        if entities:
            records = await _fetch_verification_records(data_source, entities)
            corpus_records = _format_records(records)
        # Execute pattern-verification queries against full catalog
        query_results = _run_pattern_queries(data_source, finding_desc, evidence)

    # Load all observations from the run for triangulation
    all_observations = _load_observations(run_dir) if run_dir else ""

    # Run four operations in parallel
    client = anthropic.AsyncAnthropic()
    total_cost = 0.0
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    op1, op2, op3, op4 = await asyncio.gather(
        _run_factual_requery(client, finding_desc, evidence, corpus_records, query_results),
        _run_triangulation(client, finding_desc, all_observations),
        _run_falsification(client, finding_desc, evidence, corpus_records, query_results),
        _run_surprise_scoring(client, finding_desc, briefing_text),
    )

    for op in [op1, op2, op3, op4]:
        total_cost += op["cost"]
        total_usage["input_tokens"] += op["usage"]["input_tokens"]
        total_usage["output_tokens"] += op["usage"]["output_tokens"]

    # Integration — fifth call synthesizing the four signals
    integration = await _run_integration(
        client, finding_desc,
        json.dumps(op1["result"], indent=2),
        json.dumps(op2["result"], indent=2),
        json.dumps(op3["result"], indent=2),
        json.dumps(op4["result"], indent=2),
    )
    total_cost += integration["cost"]
    total_usage["input_tokens"] += integration["usage"]["input_tokens"]
    total_usage["output_tokens"] += integration["usage"]["output_tokens"]

    result = integration["result"]

    return ValidationResult(
        finding_id=finding_id,
        original_finding=finding,
        verdict=result.get("verdict", "insufficient_evidence"),
        reasoning=result.get("reasoning", ""),
        adjusted_confidence=float(result.get("adjusted_confidence", 0.5)),
        adjusted_tier=int(result.get("adjusted_tier", 3)),
        verification_action=result.get("verification_action", ""),
        revised_finding=result.get("revised_finding"),
        raw_reasoning=json.dumps({
            "factual_requery": op1["result"],
            "triangulation": op2["result"],
            "falsification": op3["result"],
            "surprise_scoring": op4["result"],
            "integration": result,
        }, indent=2),
        factual_assessment=op1["result"],
        interpretive_assessment=op4["result"],
        is_pipeline_issue=result.get("is_pipeline_issue", False),
        pipeline_issue_reasoning=result.get("pipeline_issue_reasoning", ""),
        token_usage=total_usage,
        cost=total_cost,
    )


# --- Operation Runners ---

async def _run_factual_requery(client, finding_desc, evidence, corpus_records, query_results) -> dict:
    prompt = FACTUAL_REQUERY_PROMPT.format(
        finding_desc=finding_desc, evidence=evidence,
        corpus_records=corpus_records, query_results=query_results)
    return await _call_operation(client, prompt, max_tokens=2000)


async def _run_triangulation(client, finding_desc, all_observations) -> dict:
    prompt = TRIANGULATION_PROMPT.format(
        finding_desc=finding_desc, all_observations=all_observations[:8000])
    return await _call_operation(client, prompt, max_tokens=2000)


async def _run_falsification(client, finding_desc, evidence, corpus_records, query_results) -> dict:
    prompt = FALSIFICATION_PROMPT.format(
        finding_desc=finding_desc, evidence=evidence,
        corpus_records=corpus_records, query_results=query_results)
    # Extended thinking for falsification — benefits from longer reasoning
    return await _call_operation(client, prompt, max_tokens=8000, thinking_budget=4000)


async def _run_surprise_scoring(client, finding_desc, briefing_text) -> dict:
    prompt = SURPRISE_SCORING_PROMPT.format(
        finding_desc=finding_desc, briefing=briefing_text or "(no briefing available)")
    return await _call_operation(client, prompt, max_tokens=1000)


async def _run_integration(client, finding_desc, factual_result,
                           triangulation_result, falsification_result,
                           surprise_result) -> dict:
    prompt = INTEGRATION_PROMPT.format(
        finding_desc=finding_desc,
        factual_result=factual_result,
        triangulation_result=triangulation_result,
        falsification_result=falsification_result,
        surprise_result=surprise_result,
    )
    return await _call_operation(client, prompt, max_tokens=2000)


async def _call_operation(client, prompt: str, max_tokens: int = 2000,
                          thinking_budget: int = 0) -> dict:
    """Make a single LLM call for one validation operation."""
    kwargs = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if thinking_budget > 0:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

    try:
        response = await client.messages.create(**kwargs)
    except Exception as e:
        return {
            "result": {"error": str(e)},
            "cost": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000

    # Extract text from response (may have thinking block)
    raw_text = ""
    for block in response.content:
        if block.type == "text":
            raw_text = block.text
            break

    try:
        result = _parse_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        result = {"error": "Failed to parse", "raw": raw_text[:500]}

    return {"result": result, "cost": cost, "usage": usage}


# --- Helpers ---

def _extract_entities(finding_desc: str, evidence: str) -> list[str]:
    """Extract entity names from finding text for corpus lookup."""
    import re
    combined = finding_desc + " " + evidence
    # Look for quoted strings, capitalized names, package-name patterns
    entities = set()
    # Quoted strings
    for m in re.findall(r"'([^']+)'|\"([^\"]+)\"", combined):
        name = m[0] or m[1]
        if len(name) > 1 and len(name) < 100:
            entities.add(name)
    # Package-like names (lowercase with hyphens/dots)
    for m in re.findall(r'\b([a-z][a-z0-9._-]{2,40})\b', combined):
        if m not in ("the", "and", "for", "from", "with", "that", "this",
                     "not", "are", "was", "were", "has", "have", "been",
                     "its", "but", "all", "any", "can", "had", "each",
                     "which", "their", "will", "other", "than", "then",
                     "into", "over", "such", "after", "between", "through",
                     "null", "true", "false", "none", "string", "pattern",
                     "evidence", "confidence", "finding", "inferred",
                     "significance", "what_conflicts", "side_a", "side_b",
                     "evidence_chain", "inferred_links"):
            entities.add(m)
    # Capitalized multi-word names (company names like "Dyne Therapeutics", "GeoVax Labs")
    # Split on sentence/clause boundaries, then extract 2-3 word capitalized sequences
    cap_stopwords = {"The", "This", "That", "With", "From", "Side", "Which",
                     "Expected", "Discovery", "Evidence", "Validation", "Finding",
                     "Pattern", "Source", "Cross", "Based", "Between", "Within",
                     "Systematic", "Regulatory", "Disclosure", "Risk", "Financial"}
    for part in re.split(r'[.,()\[\]{};:\n]+', combined):
        for m in re.findall(r'\b([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){1,2})\b', part.strip()):
            words = m.split()
            if not all(w in cap_stopwords for w in words) and len(m) < 60:
                entities.add(m)
    # Names with ampersands (e.g. "Johnson & Johnson", "S&P")
    for m in re.findall(r'\b([A-Z][A-Za-z]*(?:\s*&\s*[A-Z][A-Za-z]*)+)\b', combined):
        if len(m) < 60:
            entities.add(m)
    # Names starting with digits (e.g. "10x Genomics", "3M")
    for m in re.findall(r'\b(\d+[A-Za-z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2})\b', combined):
        if len(m) > 1 and len(m) < 40:
            entities.add(m)
    return list(entities)[:20]  # cap at 20 to avoid runaway queries


def _run_pattern_queries(data_source, finding_desc: str, evidence: str) -> str:
    """Execute pattern-verification queries against the full catalog.

    Extracts numeric thresholds and field names from the finding, constructs
    COUNT queries to test pattern claims against the full corpus.
    """
    import re
    if not hasattr(data_source, '_ensure_catalog_db'):
        return "(no catalog DB available for pattern queries)"

    data_source._ensure_catalog_db()
    db = data_source._catalog_db

    # Discover valid columns
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(records)").fetchall()}
    except Exception:
        return "(cannot read schema)"

    combined = finding_desc + " " + evidence
    results = []

    # Extract field-value patterns from the finding text
    # e.g., "maintainer_count = 1", "monthly_downloads > 1000000"
    field_patterns = re.findall(
        r'(\w+)\s*(=|>|<|>=|<=)\s*([\d,]+)', combined)

    for field, op, value in field_patterns:
        field_lower = field.lower()
        # Match against actual column names
        matched_col = None
        for col in cols:
            if col.lower() == field_lower or col.lower().replace("_", "") == field_lower.replace("_", ""):
                matched_col = col
                break
        if not matched_col:
            continue
        clean_value = value.replace(",", "")
        try:
            sql = f"SELECT COUNT(*) FROM records WHERE {matched_col} {op} {clean_value}"
            count = db.execute(sql).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            results.append(f"Query: {matched_col} {op} {clean_value} → {count:,} of {total:,} records ({count/total*100:.1f}%)")
        except Exception:
            continue

    # Also run some standard distribution queries for fields mentioned in the finding
    for col in cols:
        if col.lower() in combined.lower() and col not in ("name", "description", "keywords", "repository", "dependencies", "maintainers"):
            try:
                row = db.execute(f"SELECT MIN({col}), MAX({col}), AVG({col}) FROM records WHERE {col} IS NOT NULL").fetchone()
                if row and row[0] is not None:
                    results.append(f"Distribution: {col} min={row[0]}, max={row[1]}, avg={row[2]:.1f}")
            except Exception:
                continue

    return "\n".join(results) if results else "(no pattern queries matched)"


async def _fetch_verification_records(data_source, entities: list[str]) -> list[dict]:
    """Fetch corpus records matching the entities named in the finding."""
    records = []
    if hasattr(data_source, '_ensure_catalog_db'):
        data_source._ensure_catalog_db()
        db = data_source._catalog_db

        # Discover which columns exist for entity lookup
        try:
            cursor = db.execute("PRAGMA table_info(records)")
            columns = {row[1] for row in cursor.fetchall()}
        except Exception:
            columns = set()

        # Try identity columns in priority order
        id_columns = [c for c in ("name", "company", "title", "id") if c in columns]

        for entity in entities[:10]:
            for col in id_columns:
                try:
                    rows = db.execute(
                        f"SELECT * FROM records WHERE {col} = ? LIMIT 3",
                        (entity,)
                    ).fetchall()
                    for r in rows:
                        records.append(dict(r))
                    if rows:
                        break  # found match on this column, skip remaining columns
                except Exception:
                    continue
            # LIKE fallback for partial matches (e.g. "Dyne Therapeutics" vs "Dyne Therapeutics, Inc.")
            if not any(entity.lower() in str(r.get(col, "")).lower()
                       for r in records for col in id_columns):
                for col in id_columns:
                    try:
                        rows = db.execute(
                            f"SELECT * FROM records WHERE {col} LIKE ? LIMIT 3",
                            (f"%{entity}%",)
                        ).fetchall()
                        for r in rows:
                            records.append(dict(r))
                        if rows:
                            break
                    except Exception:
                        continue

    if not records and hasattr(data_source, 'fetch'):
        # Fallback to keyword search
        try:
            records = await data_source.fetch(
                {"keyword": entities[0] if entities else ""},
                max_results=10)
        except Exception:
            pass
    return records[:20]


def _format_records(records: list[dict]) -> str:
    """Format fetched records for prompt context."""
    if not records:
        return "(no corpus records fetched — claims cannot be checked against data)"
    lines = []
    for rec in records[:20]:
        parts = []
        for k, v in rec.items():
            if v is not None and v != "" and v != []:
                val_str = str(v)
                if len(val_str) > 500:
                    val_str = val_str[:500] + f"... [{len(val_str):,} chars total]"
                parts.append(f"{k}: {val_str}")
        lines.append("{" + ", ".join(parts) + "}")
    return "\n".join(lines)


def _load_observations(run_dir: str) -> str:
    """Load all observations from a run's node files for triangulation."""
    from pathlib import Path
    import os
    nodes_dir = Path(run_dir) / "nodes"
    if not nodes_dir.exists():
        return "(no observation data available)"
    lines = []
    for f in sorted(os.listdir(nodes_dir)):
        if not f.endswith(".json"):
            continue
        try:
            node = json.load(open(nodes_dir / f))
            pos = node.get("tree_position", "?")
            role = node.get("role", "?")
            for obs in node.get("observations", []):
                evidence = obs.get("raw_evidence", "")[:200]
                hypothesis = obs.get("local_hypothesis", "")[:100]
                lines.append(f"[{pos} / {role}] {evidence} — {hypothesis}")
        except Exception:
            continue
    return "\n".join(lines) if lines else "(no observations found)"


async def check_charter_shape(finding_claim: str, charter_exclusions: str,
                              charter_text: str = "") -> dict:
    """Check whether a finding is on-bar for this engagement given the charter.

    Uses charter-grounded reasoning rather than pattern-name matching.
    A finding is excluded only if it genuinely falls outside the charter's
    scope — not because its name resembles an exclusion pattern.

    Returns:
        dict with verdict, matched_exclusion, reasoning, recommended_action
    """
    if not charter_text and not charter_exclusions:
        return {
            "verdict": "no_check",
            "matched_exclusion": None,
            "reasoning": "No charter available for checking",
            "recommended_action": "pass",
            "cost": 0,
        }

    charter_context = charter_text or ""
    exclusion_context = f"\n\nEXCLUDED PATTERNS:\n{charter_exclusions}" if charter_exclusions else ""

    prompt = (
        f"Given this charter, is this finding on-bar for the engagement?\n\n"
        f"CHARTER:\n{charter_context[:3000]}{exclusion_context}\n\n"
        f"FINDING:\n{finding_claim}\n\n"
        f"The question is NOT whether the finding's name resembles an excluded pattern. "
        f"The question is whether this finding, given its specific evidence and claims, "
        f"contributes something substantive to this engagement's goals.\n\n"
        f"A finding with specific entities, specific numbers, and verifiable evidence "
        f"should usually PASS even if its topic overlaps with a general exclusion category. "
        f"Only exclude findings that genuinely add nothing beyond what the charter already "
        f"covers as common knowledge.\n\n"
        f"Return JSON:\n"
        f'{{"verdict": "on_bar | off_bar | borderline", '
        f'"matched_exclusion": "specific exclusion it matches, or null", '
        f'"reasoning": "why this finding does or does not serve the charter goals", '
        f'"recommended_action": "pass | weaken | reject"}}'
    )

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        cost = (response.usage.input_tokens * 3 / 1_000_000 +
                response.usage.output_tokens * 15 / 1_000_000)
        raw = response.content[0].text
        result = _parse_json(raw)
        result["cost"] = cost
        result["raw_reasoning"] = raw
        return result
    except Exception as e:
        return {
            "verdict": "error",
            "matched_exclusion": None,
            "reasoning": f"Charter-shape check failed: {e}",
            "recommended_action": "pass",
            "cost": 0,
        }


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return json.loads(text[start:end].strip())
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("Could not extract JSON")
