"""Base interface for data sources.

Every data source must implement two capabilities:
1. survey() — return structural metadata (what's here, how much, what shape)
2. fetch() — return actual content for a given scope

Catalog query capability is provided by the base class. Any data source
that produces a JSONL catalog via fetch_bulk_metadata gets SQLite indexing
and structured querying for free. No corpus-specific logic in the query layer.
"""

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path


class DataSource(ABC):
    """Interface that all data source connectors implement."""

    def __init__(self):
        self._catalog_db = None

    @abstractmethod
    async def survey(self, filters: dict) -> dict:
        """Get structural metadata: counts, categories, date ranges, entities.

        This is the "shelf labels" scan — tells the node what's here
        without fetching full content. Should be cheap and fast.
        """
        ...

    @abstractmethod
    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch actual documents/records matching the filters.

        Returns list of document dicts. Full content fetching happens
        only when a node decides it needs depth.
        """
        ...

    @abstractmethod
    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single document's full content by ID."""
        ...

    def filter_schema(self) -> dict:
        """Describe what filter parameters this data source accepts.

        Returns a structured contract so the LLM knows what queries will work.
        Each parameter is described with type, description, example, and required flag.
        Shape:
            {
                "parameter_name": {
                    "type": "string" | "list[string]" | "integer" | ...,
                    "description": "what this filter does",
                    "example": <concrete example value>,
                    "required": False
                }
            }

        A new connector for any corpus (sensors, wikis, databases) fills in
        the same structure. Pipeline code never inspects parameter names.
        """
        return {}

    def valid_filter_params(self) -> set[str]:
        """Return the set of all valid top-level filter parameter names.

        Includes schema keys plus any catalog field names that are accepted
        as flat top-level params. Override in subclass to add catalog fields.
        """
        return set(self.filter_schema().keys())

    def catalog_path(self) -> Path | None:
        """Return path to this data source's enriched JSONL catalog, or None.

        Override in subclass if the catalog lives at a non-standard location.
        Default: looks for catalog/{source_name}_enriched.jsonl
        """
        # Derive from class name: NpmRegistrySource → npm, SecEdgarSource → sec
        name = self.__class__.__name__.lower()
        for suffix in ("source", "registrysource", "registry"):
            name = name.removesuffix(suffix)
        candidates = [
            Path(f"catalog/{name}_enriched.jsonl"),
            Path(f"catalog/{name}_catalog.jsonl"),
        ]
        for p in candidates:
            if p.exists() and p.stat().st_size > 1000:
                return p
        return None

    # === Catalog query infrastructure (corpus-agnostic) ===

    def _ensure_catalog_db(self):
        """Build SQLite index over the enriched JSONL catalog. Lazy — first query."""
        if self._catalog_db is not None:
            return

        jsonl_path = self.catalog_path()
        if jsonl_path is None:
            self._catalog_db = sqlite3.connect(":memory:")
            return

        db_path = jsonl_path.with_suffix(".db")

        # Reuse existing DB if it's newer than the JSONL
        if db_path.exists() and db_path.stat().st_mtime >= jsonl_path.stat().st_mtime:
            self._catalog_db = sqlite3.connect(str(db_path))
            self._catalog_db.row_factory = sqlite3.Row
            return

        print(f"  [CATALOG DB] Building SQLite index from {jsonl_path}...")

        # Read first record to discover schema
        with open(jsonl_path) as f:
            first_line = f.readline().strip()
        if not first_line:
            self._catalog_db = sqlite3.connect(":memory:")
            return
        sample = json.loads(first_line)

        # Infer column types from sample record
        columns = []
        col_names = []
        for key, value in sample.items():
            col_names.append(key)
            if isinstance(value, int):
                columns.append(f"{key} INTEGER")
            elif isinstance(value, float):
                columns.append(f"{key} REAL")
            elif isinstance(value, (list, dict)):
                columns.append(f"{key} TEXT")  # stored as JSON
            else:
                columns.append(f"{key} TEXT")

        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE IF EXISTS records")
        conn.execute(f"CREATE TABLE records ({', '.join(columns)})")

        # Bulk insert
        placeholders = ",".join("?" * len(col_names))
        batch = []
        count = 0
        with open(jsonl_path) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                row = []
                for key in col_names:
                    val = r.get(key)
                    if isinstance(val, (list, dict)):
                        row.append(json.dumps(val))
                    else:
                        row.append(val)
                batch.append(tuple(row))
                if len(batch) >= 5000:
                    conn.executemany(f"INSERT OR REPLACE INTO records VALUES ({placeholders})", batch)
                    batch = []
                    count += len(batch)
            if batch:
                conn.executemany(f"INSERT OR REPLACE INTO records VALUES ({placeholders})", batch)

        # Create indices on numeric and commonly-filtered columns
        for key, value in sample.items():
            if isinstance(value, (int, float)):
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{key} ON records({key})")
            elif key in ("name", "id", "license", "type", "category", "status"):
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{key} ON records({key})")

        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        print(f"  [CATALOG DB] Indexed {total} records ({len(col_names)} fields)")

        conn.row_factory = sqlite3.Row
        self._catalog_db = conn

    def query_catalog(self, query: dict, max_results: int = 500) -> list[dict]:
        """Query the catalog SQLite index. Returns matching records.

        Query is a dict of field→condition pairs. Conditions:
          - Simple value: field = value (equality)
          - Dict with operator: {"gt": N}, {"lt": N}, {"gte": N}, {"lte": N}
          - Dict with "in": [values] (set membership)
          - Dict with "contains": value (substring/element match)
          - Dict with "between": [low, high] (range)

        Corpus-agnostic — works on any enriched JSONL catalog regardless
        of what fields it contains.
        """
        self._ensure_catalog_db()

        # Discover valid columns from the DB
        try:
            cursor = self._catalog_db.execute("PRAGMA table_info(records)")
            valid_fields = {row[1] for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            return []

        where_clauses = []
        params = []
        skipped_fields = [f for f in query if f not in valid_fields]
        if skipped_fields:
            print(f"  [CATALOG QUERY] Unknown fields ignored: {skipped_fields}. "
                  f"Valid fields: {sorted(valid_fields)}")

        for field, condition in query.items():
            if field not in valid_fields:
                continue

            if isinstance(condition, dict):
                if "gt" in condition:
                    where_clauses.append(f"{field} > ?")
                    params.append(condition["gt"])
                elif "gte" in condition:
                    where_clauses.append(f"{field} >= ?")
                    params.append(condition["gte"])
                elif "lt" in condition:
                    where_clauses.append(f"{field} < ?")
                    params.append(condition["lt"])
                elif "lte" in condition:
                    where_clauses.append(f"{field} <= ?")
                    params.append(condition["lte"])
                elif "in" in condition:
                    placeholders = ",".join("?" * len(condition["in"]))
                    where_clauses.append(f"{field} IN ({placeholders})")
                    params.extend(condition["in"])
                elif "contains" in condition:
                    where_clauses.append(f"{field} LIKE ?")
                    params.append(f"%{condition['contains']}%")
                elif "between" in condition and len(condition["between"]) == 2:
                    where_clauses.append(f"{field} BETWEEN ? AND ?")
                    params.extend(condition["between"])
            else:
                where_clauses.append(f"{field} = ?")
                params.append(condition)

        if not where_clauses:
            return []

        sql = f"SELECT * FROM records WHERE {' AND '.join(where_clauses)} LIMIT ?"
        params.append(max_results)

        try:
            rows = self._catalog_db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

        # Convert rows back to dicts, parsing JSON fields
        results = []
        for row in rows:
            record = dict(row)
            for key, val in record.items():
                if isinstance(val, str) and val.startswith("["):
                    try:
                        record[key] = json.loads(val)
                    except (ValueError, TypeError):
                        pass
            results.append(record)

        return results

    def catalog_metadata(self) -> dict:
        """Return metadata about the catalog for node reasoning.

        Corpus-agnostic — discovers fields and ranges from whatever
        the catalog contains.
        """
        self._ensure_catalog_db()
        try:
            total = self._catalog_db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            cursor = self._catalog_db.execute("PRAGMA table_info(records)")
            columns = [(row[1], row[2]) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            return {"total_records": 0, "fields": []}

        field_info = []
        for col_name, col_type in columns:
            info = {"name": col_name, "type": col_type}
            try:
                if col_type in ("INTEGER", "REAL"):
                    row = self._catalog_db.execute(
                        f"SELECT MIN({col_name}), MAX({col_name}) FROM records"
                    ).fetchone()
                    if row:
                        info["min"] = row[0]
                        info["max"] = row[1]
                elif col_type == "TEXT" and col_name in ("license", "type", "category", "status"):
                    rows = self._catalog_db.execute(
                        f"SELECT {col_name}, COUNT(*) as cnt FROM records "
                        f"WHERE {col_name} != '' GROUP BY {col_name} ORDER BY cnt DESC LIMIT 5"
                    ).fetchall()
                    info["top_values"] = [{"value": r[0], "count": r[1]} for r in rows]
            except sqlite3.OperationalError:
                pass
            field_info.append(info)

        return {
            "total_records": total,
            "fields": field_info,
        }
