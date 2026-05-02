"""EQUIP Translator — converts natural-language partition descriptions to SQL.

The translator is a guarded loop:
  Stage 1: Author SQL from partition + schema context
  Stage 2: Static schema check (column names exist)
  Stage 3: Execute against catalog
  Stage 4: Sanity-check record count (revise if extreme)
  Stage 5: Return records + interpretation + diagnostics

Every translation is logged to output/{run_id}/translations/{hire_id}.json.
"""

import json
import re
import sqlite3
import time
from pathlib import Path

import anthropic


class TranslationResult:
    """Result of a partition translation."""

    def __init__(self):
        self.sql = ""
        self.interpretation = ""
        self.records = []
        self.record_count = 0
        self.total_in_slice = None  # unlimited count of matching records (None = unknown)
        self.slice_distributions = ""  # field distributions within the slice
        self.notes = []
        self.stages = []  # log of every stage fired
        self.cost = 0.0
        self.success = True
        self.error = ""

    def to_dict(self) -> dict:
        return {
            "sql": self.sql,
            "interpretation": self.interpretation,
            "record_count": self.record_count,
            "total_in_slice": self.total_in_slice,
            "slice_distributions": self.slice_distributions[:200] if self.slice_distributions else "",
            "notes": self.notes,
            "stages": self.stages,
            "cost": self.cost,
            "success": self.success,
            "error": self.error,
        }


async def translate_partition(
    partition: str,
    data_source,
    max_records: int = 100,
    run_dir: str = None,
    hire_id: str = None,
) -> TranslationResult:
    """Translate a natural-language partition description into catalog records.

    Args:
        partition: natural-language description of the data slice
        data_source: the corpus data source (has catalog_path, catalog_metadata, _ensure_catalog_db)
        max_records: max records to return
        run_dir: if provided, write translation log
        hire_id: identifier for logging

    Returns:
        TranslationResult with records, SQL, interpretation, diagnostics
    """
    result = TranslationResult()
    start_time = time.time()

    # Get schema context
    schema_ctx = _build_schema_context(data_source)
    if not schema_ctx["valid"]:
        result.success = False
        result.error = f"Cannot build schema context: {schema_ctx.get('error', 'no catalog')}"
        result.stages.append({"stage": "schema", "outcome": "failed", "error": result.error})
        _save_log(result, partition, run_dir, hire_id, start_time)
        return result

    valid_columns = schema_ctx["columns"]
    client = anthropic.AsyncAnthropic()

    # === Stage 1 + 2 + 3: Author → Schema check → Execute (with retry) ===
    for attempt in range(2):
        stage1_result = await _stage_author(client, partition, schema_ctx, result)
        if not stage1_result:
            continue

        sql = stage1_result["sql"]
        result.interpretation = stage1_result["interpretation"]
        result.sql = sql

        # Stage 2: Static schema check
        schema_ok, bad_columns = _stage_schema_check(sql, valid_columns)
        if not schema_ok:
            note = f"Attempt {attempt+1}: unknown columns {bad_columns}. Valid: {sorted(valid_columns)}"
            result.stages.append({"stage": "schema_check", "outcome": "failed",
                                  "bad_columns": bad_columns, "attempt": attempt + 1})
            result.notes.append(note)
            # Retry with error context
            schema_ctx["error_context"] = note
            continue

        result.stages.append({"stage": "schema_check", "outcome": "passed", "attempt": attempt + 1})

        # Stage 3: Execute
        try:
            data_source._ensure_catalog_db()
            # Add LIMIT to prevent runaway queries
            exec_sql = sql.rstrip("; \n")
            if "LIMIT" not in exec_sql.upper():
                exec_sql += f" LIMIT {max_records}"

            rows = data_source._catalog_db.execute(exec_sql).fetchall()
            records = [dict(r) for r in rows]

            # Parse JSON fields back
            for rec in records:
                for key, val in rec.items():
                    if isinstance(val, str) and val.startswith("["):
                        try:
                            rec[key] = json.loads(val)
                        except (ValueError, TypeError):
                            pass

            result.records = records
            result.record_count = len(records)
            result.stages.append({"stage": "execute", "outcome": "success",
                                  "record_count": len(records), "attempt": attempt + 1})
            break  # Success — exit retry loop

        except sqlite3.OperationalError as e:
            note = f"Attempt {attempt+1}: SQL error: {e}"
            result.stages.append({"stage": "execute", "outcome": "failed",
                                  "error": str(e), "attempt": attempt + 1})
            result.notes.append(note)
            schema_ctx["error_context"] = note
            continue

    else:
        # Both attempts failed
        result.success = False
        result.error = "CANNOT_TRANSLATE: failed after 2 attempts"
        result.stages.append({"stage": "final", "outcome": "CANNOT_TRANSLATE"})
        _save_log(result, partition, run_dir, hire_id, start_time)
        return result

    # === Stage 3b: Unlimited count (slice cardinality) ===
    try:
        count_sql = re.sub(
            r'^\s*SELECT\s+.+?\s+FROM\s+',
            'SELECT COUNT(*) FROM ',
            result.sql,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Strip any LIMIT from the count query
        count_sql = re.sub(r'\s+LIMIT\s+\d+', '', count_sql, flags=re.IGNORECASE)
        total = data_source._catalog_db.execute(count_sql).fetchone()[0]
        result.total_in_slice = total
    except Exception:
        result.total_in_slice = None  # unknown — worker sees "100 of unknown"

    # === Stage 3c: Slice distributions ===
    try:
        from .equip import _build_distributions
        # Extract WHERE clause from the SQL for scoped distributions
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+ORDER|\s+LIMIT|\s*$)',
                                result.sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            result.slice_distributions = _build_distributions(
                data_source, where_clause=where_match.group(1).strip())
        else:
            result.slice_distributions = _build_distributions(data_source)
    except Exception:
        result.slice_distributions = ""

    # === Stage 4: Record count note (no revision) ===
    if result.record_count == 0:
        result.notes.append(f"Partition returned 0 records — the engagement lead's filter matched nothing")
        result.stages.append({"stage": "count_note", "record_count": 0})
    elif result.record_count < 5:
        result.notes.append(f"Partition returned {result.record_count} records — small but faithful to description")
        result.stages.append({"stage": "count_note", "record_count": result.record_count})

    # === Stage 5: Return ===
    result.stages.append({"stage": "complete", "record_count": result.record_count})
    _save_log(result, partition, run_dir, hire_id, start_time)
    return result


def _build_schema_context(data_source) -> dict:
    """Build the schema context the translator LLM needs."""
    catalog_path = data_source.catalog_path() if hasattr(data_source, 'catalog_path') else None
    if not catalog_path or not catalog_path.exists():
        return {"valid": False, "error": "no catalog"}

    data_source._ensure_catalog_db()

    try:
        cursor = data_source._catalog_db.execute("PRAGMA table_info(records)")
        columns_info = cursor.fetchall()
    except sqlite3.OperationalError:
        return {"valid": False, "error": "cannot read table schema"}

    columns = {row[1] for row in columns_info}
    columns_desc = []
    for row in columns_info:
        col_name, col_type = row[1], row[2]
        columns_desc.append(f"  {col_name} ({col_type})")

    # Get sample rows
    try:
        sample_rows = data_source._catalog_db.execute(
            "SELECT * FROM records ORDER BY monthly_downloads DESC LIMIT 3"
        ).fetchall()
        samples = [dict(r) for r in sample_rows]
    except Exception:
        samples = []

    # Get total count
    try:
        total = data_source._catalog_db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    except Exception:
        total = 0

    # Get catalog metadata for ranges
    meta = data_source.catalog_metadata() if hasattr(data_source, 'catalog_metadata') else {}

    return {
        "valid": True,
        "table_name": "records",
        "columns": columns,
        "columns_desc": "\n".join(columns_desc),
        "samples": samples,
        "total_records": total,
        "metadata": meta,
        "error_context": "",  # populated on retry
    }


async def _stage_author(client, partition: str, schema_ctx: dict,
                        result: TranslationResult) -> dict | None:
    """Stage 1: LLM authors SQL from partition description."""

    # Format sample rows
    samples_text = ""
    for i, s in enumerate(schema_ctx.get("samples", [])[:3], 1):
        samples_text += f"  Row {i}: {json.dumps(s, default=str)[:300]}\n"

    # Format field ranges from metadata
    ranges_text = ""
    for f in schema_ctx.get("metadata", {}).get("fields", []):
        if f.get("min") is not None:
            ranges_text += f"  {f['name']}: {f['min']} to {f['max']}\n"
        elif f.get("top_values"):
            vals = ", ".join(f"{v['value']} ({v['count']})" for v in f["top_values"][:3])
            ranges_text += f"  {f['name']}: top values: {vals}\n"

    error_ctx = schema_ctx.get("error_context", "")
    retry_note = f"\nPREVIOUS ATTEMPT FAILED: {error_ctx}\nFix the issue and try again.\n" if error_ctx else ""

    prompt = f"""Translate this natural-language data partition description into a SQL query.

PARTITION DESCRIPTION: {partition}

TABLE: records ({schema_ctx['total_records']} rows)

COLUMNS:
{schema_ctx['columns_desc']}

FIELD RANGES:
{ranges_text}

SAMPLE ROWS:
{samples_text}
{retry_note}
EXAMPLES:
- "packages maintained by single individuals with monthly downloads above 1M"
  → SELECT * FROM records WHERE maintainer_count = 1 AND monthly_downloads > 1000000

- "packages created before 2020 with last_modified varying across the past 5 years"
  → SELECT * FROM records WHERE created < '2020-01-01' AND last_modified > '2021-01-01'

- "packages with dependency_count above 50"
  → SELECT * FROM records WHERE dependency_count > 50

Return JSON:
{{"sql": "SELECT * FROM records WHERE ...", "interpretation": "one sentence describing what this query fetches"}}

Use ONLY column names from the COLUMNS list above. Return valid JSON only."""

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        cost = (response.usage.input_tokens * 3 / 1_000_000 +
                response.usage.output_tokens * 15 / 1_000_000)
        result.cost += cost

        # Parse JSON
        parsed = _parse_json(raw)
        result.stages.append({"stage": "author", "outcome": "success",
                              "sql": parsed.get("sql", ""), "cost": cost})
        return parsed

    except Exception as e:
        result.stages.append({"stage": "author", "outcome": "failed", "error": str(e)})
        result.notes.append(f"Author stage failed: {e}")
        return None


def _stage_schema_check(sql: str, valid_columns: set) -> tuple[bool, list]:
    """Stage 2: Check that all column references in SQL exist in the schema."""
    # Extract potential column names from SQL
    # Look for identifiers in WHERE, SELECT, ORDER BY, GROUP BY clauses
    # Simple approach: find all word tokens that could be column names
    sql_upper = sql.upper()
    # Remove string literals
    cleaned = re.sub(r"'[^']*'", "", sql)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)

    # Find tokens that look like column references
    tokens = re.findall(r'\b([a-z_][a-z_0-9]*)\b', cleaned.lower())
    # Filter out SQL keywords
    sql_keywords = {
        'select', 'from', 'where', 'and', 'or', 'not', 'in', 'like', 'between',
        'is', 'null', 'order', 'by', 'asc', 'desc', 'limit', 'count', 'sum',
        'avg', 'min', 'max', 'group', 'having', 'as', 'on', 'join', 'left',
        'right', 'inner', 'outer', 'distinct', 'case', 'when', 'then', 'else',
        'end', 'cast', 'integer', 'text', 'real', 'records', 'true', 'false',
        'replace', 'substr', 'instr', 'length', 'upper', 'lower', 'trim',
        'coalesce', 'ifnull', 'typeof', 'exists', 'union', 'all', 'offset',
        'json_extract', 'json', 'glob',
    }
    potential_columns = {t for t in tokens if t not in sql_keywords}

    bad_columns = [c for c in potential_columns if c not in valid_columns]
    return (len(bad_columns) == 0, bad_columns)


async def _stage_sanity_check(client, partition: str, sql: str, count: int,
                              schema_ctx: dict, data_source, max_records: int,
                              result: TranslationResult) -> dict | None:
    """Stage 4: If record count is extreme, ask LLM to revise."""
    direction = "too narrow (< 5 records)" if count < 5 else f"very broad ({count}+ records)"

    prompt = f"""Your SQL query for this partition returned an unusual number of records.

PARTITION: {partition}
SQL: {sql}
RECORD COUNT: {count}
ISSUE: The count seems {direction} for this partition.

TABLE has {schema_ctx['total_records']} total records.

Should the query be revised? If yes, return revised SQL. If the count is actually reasonable for this partition, return the same SQL.

Return JSON: {{"sql": "...", "interpretation": "...", "revised": true/false, "reasoning": "why"}}"""

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        cost = (response.usage.input_tokens * 3 / 1_000_000 +
                response.usage.output_tokens * 15 / 1_000_000)
        result.cost += cost

        parsed = _parse_json(response.content[0].text.strip())
        result.stages.append({"stage": "sanity_check", "outcome": "revised" if parsed.get("revised") else "kept",
                              "original_count": count, "reasoning": parsed.get("reasoning", "")})

        if parsed.get("revised") and parsed.get("sql") != sql:
            # Re-execute with revised SQL
            new_sql = parsed["sql"].rstrip("; \n")
            if "LIMIT" not in new_sql.upper():
                new_sql += f" LIMIT {max_records}"
            try:
                rows = data_source._catalog_db.execute(new_sql).fetchall()
                records = [dict(r) for r in rows]
                for rec in records:
                    for key, val in rec.items():
                        if isinstance(val, str) and val.startswith("["):
                            try:
                                rec[key] = json.loads(val)
                            except (ValueError, TypeError):
                                pass
                result.notes.append(f"Sanity check revised: {count} → {len(records)} records")
                return {
                    "sql": parsed["sql"],
                    "records": records,
                    "record_count": len(records),
                    "interpretation": parsed.get("interpretation", result.interpretation),
                }
            except sqlite3.OperationalError as e:
                result.notes.append(f"Sanity check revision failed: {e}, keeping original")
                return None

        return None

    except Exception as e:
        result.notes.append(f"Sanity check failed: {e}")
        result.stages.append({"stage": "sanity_check", "outcome": "error", "error": str(e)})
        return None


def _save_log(result: TranslationResult, partition: str,
              run_dir: str, hire_id: str, start_time: float):
    """Save translation log to diagnostics directory."""
    if not run_dir or not hire_id:
        return
    log_dir = Path(run_dir) / "translations"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{hire_id}.json"
    log_data = {
        "partition": partition,
        "hire_id": hire_id,
        **result.to_dict(),
        "elapsed_seconds": round(time.time() - start_time, 2),
    }
    log_path.write_text(json.dumps(log_data, indent=2, default=str))


def _parse_json(text: str) -> dict:
    """Extract JSON from LLM response text."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        if e > s:
            return json.loads(text[s:e].strip())
    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        return json.loads(text[s:e])
    raise ValueError(f"Could not extract JSON from: {text[:200]}")
