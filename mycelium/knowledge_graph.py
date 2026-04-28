"""Knowledge Graph — persistent, queryable graph of discovered knowledge.

Built during exploration from node observations. Each observation extracts
entities and relationships. After exploration, the graph can be queried
to answer questions that traverse relationships, not just match text.

Storage: SQLite (no external dependencies).
Persists across runs — new explorations ADD to the graph.
"""

import json
import sqlite3
import uuid
from pathlib import Path

import anthropic


class KnowledgeGraph:
    """SQLite-backed knowledge graph."""

    def __init__(self, db_path: str = "knowledge.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                entity_type TEXT,
                properties TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                source_node_id TEXT,
                source_run_id TEXT,
                confidence REAL DEFAULT 0.5,
                observation_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id);

            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY,
                from_entity TEXT NOT NULL,
                to_entity TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                evidence TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (from_entity) REFERENCES entities(id),
                FOREIGN KEY (to_entity) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity);
            CREATE INDEX IF NOT EXISTS idx_rel_to ON relationships(to_entity);
            CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relationship_type);

            CREATE TABLE IF NOT EXISTS contradictions (
                id TEXT PRIMARY KEY,
                entity_id TEXT,
                claim_a TEXT NOT NULL,
                claim_b TEXT NOT NULL,
                source_a TEXT,
                source_b TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Reasoning quality tables: persist learning across runs

            CREATE TABLE IF NOT EXISTS role_records (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                parent_id TEXT,
                role_name TEXT,
                mission TEXT,
                bar TEXT,
                heuristic TEXT,
                scope_description TEXT,
                budget REAL,
                corpus TEXT,
                tree_position TEXT,
                depth INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_role_run ON role_records(run_id);
            CREATE INDEX IF NOT EXISTS idx_role_corpus ON role_records(corpus);

            CREATE TABLE IF NOT EXISTS decision_records (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                decision_type TEXT,
                outcome TEXT,
                reasoning_summary TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_decision_run ON decision_records(run_id);
            CREATE INDEX IF NOT EXISTS idx_decision_type ON decision_records(decision_type);

            CREATE TABLE IF NOT EXISTS outcome_records (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                observation_count INTEGER DEFAULT 0,
                budget_allocated REAL,
                budget_spent REAL,
                turn2_classification TEXT,
                reader_test_scores TEXT DEFAULT '{}',
                validation_outcomes TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_outcome_run ON outcome_records(run_id);
            CREATE INDEX IF NOT EXISTS idx_outcome_turn2 ON outcome_records(turn2_classification);
        """)
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Add columns introduced in schema v2. Safe to run on existing DBs."""
        migrations = [
            # Entities: canonical name, attributes, run tracking, observation count
            "ALTER TABLE entities ADD COLUMN canonical_name TEXT",
            "ALTER TABLE entities ADD COLUMN attributes TEXT DEFAULT '{}'",
            "ALTER TABLE entities ADD COLUMN first_observed_run TEXT",
            "ALTER TABLE entities ADD COLUMN last_observed_run TEXT",
            "ALTER TABLE entities ADD COLUMN observation_count INTEGER DEFAULT 0",
            # Relationships: structured attributes, provenance, multiplicity
            "ALTER TABLE relationships ADD COLUMN attributes TEXT DEFAULT '{}'",
            "ALTER TABLE relationships ADD COLUMN provenance TEXT",
            "ALTER TABLE relationships ADD COLUMN multiplicity TEXT",
            # Corpus separation: tag entities, observations, relationships by data source
            "ALTER TABLE entities ADD COLUMN corpus TEXT",
            "ALTER TABLE observations ADD COLUMN corpus TEXT",
            "ALTER TABLE relationships ADD COLUMN corpus TEXT",
        ]
        for sql in migrations:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # Column already exists
        self.conn.commit()

    # --- Entity / observation / relationship methods ---

    def add_entity(self, name: str, entity_type: str = "unknown",
                   properties: dict = None, canonical_name: str = None,
                   attributes: dict = None, run_id: str = None,
                   corpus: str = None) -> str:
        """Add or update an entity. Returns entity ID."""
        # Check if entity already exists
        existing = self.conn.execute(
            "SELECT id FROM entities WHERE name = ?", (name,)
        ).fetchone()

        if existing:
            entity_id = existing["id"]
            if properties:
                # Merge properties
                old_props = json.loads(
                    self.conn.execute("SELECT properties FROM entities WHERE id = ?",
                                     (entity_id,)).fetchone()["properties"])
                old_props.update(properties)
                self.conn.execute(
                    "UPDATE entities SET properties = ?, entity_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(old_props), entity_type, entity_id))
            if attributes:
                old_attrs = json.loads(
                    self.conn.execute("SELECT attributes FROM entities WHERE id = ?",
                                     (entity_id,)).fetchone()["attributes"])
                old_attrs.update(attributes)
                self.conn.execute(
                    "UPDATE entities SET attributes = ? WHERE id = ?",
                    (json.dumps(old_attrs), entity_id))
            if canonical_name:
                self.conn.execute(
                    "UPDATE entities SET canonical_name = ? WHERE id = ?",
                    (canonical_name, entity_id))
            if run_id:
                self.conn.execute(
                    "UPDATE entities SET last_observed_run = ? WHERE id = ?",
                    (run_id, entity_id))
            if corpus:
                self.conn.execute(
                    "UPDATE entities SET corpus = ? WHERE id = ?",
                    (corpus, entity_id))
            return entity_id

        entity_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO entities (id, name, entity_type, properties,
               canonical_name, attributes, first_observed_run, last_observed_run,
               observation_count, corpus)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (entity_id, name, entity_type, json.dumps(properties or {}),
             canonical_name or name, json.dumps(attributes or {}), run_id, run_id,
             corpus))
        self.conn.commit()
        return entity_id

    def add_observation(self, entity_name: str, claim: str,
                        source_node_id: str = "", source_run_id: str = "",
                        confidence: float = 0.5, observation_type: str = "pattern",
                        entity_type: str = "unknown",
                        corpus: str = None) -> str:
        """Add an observation about an entity."""
        entity_id = self.add_entity(entity_name, entity_type, run_id=source_run_id, corpus=corpus)
        obs_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO observations (id, entity_id, claim, source_node_id, source_run_id, confidence, observation_type, corpus) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (obs_id, entity_id, claim, source_node_id, source_run_id, confidence, observation_type, corpus))
        # Maintain observation_count on the entity
        self.conn.execute(
            "UPDATE entities SET observation_count = observation_count + 1 WHERE id = ?",
            (entity_id,))
        self.conn.commit()
        return obs_id

    def add_relationship(self, from_name: str, to_name: str,
                         relationship_type: str, confidence: float = 0.5,
                         evidence: str = "",
                         from_type: str = "unknown", to_type: str = "unknown",
                         attributes: dict = None, provenance: str = None,
                         multiplicity: str = None,
                         corpus: str = None) -> str:
        """Add a relationship between two entities."""
        from_id = self.add_entity(from_name, from_type, corpus=corpus)
        to_id = self.add_entity(to_name, to_type, corpus=corpus)

        # Check for existing relationship
        existing = self.conn.execute(
            "SELECT id, evidence FROM relationships WHERE from_entity = ? AND to_entity = ? AND relationship_type = ?",
            (from_id, to_id, relationship_type)).fetchone()

        if existing:
            # Append evidence
            old_evidence = json.loads(existing["evidence"])
            old_evidence.append(evidence)
            updates = ["evidence = ?", "confidence = MAX(confidence, ?)"]
            params = [json.dumps(old_evidence), confidence]
            if provenance:
                updates.append("provenance = ?")
                params.append(provenance)
            if multiplicity:
                updates.append("multiplicity = ?")
                params.append(multiplicity)
            if attributes:
                updates.append("attributes = ?")
                params.append(json.dumps(attributes))
            params.append(existing["id"])
            self.conn.execute(
                f"UPDATE relationships SET {', '.join(updates)} WHERE id = ?", params)
            self.conn.commit()
            return existing["id"]

        rel_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO relationships (id, from_entity, to_entity, relationship_type,
               confidence, evidence, attributes, provenance, multiplicity, corpus)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rel_id, from_id, to_id, relationship_type, confidence,
             json.dumps([evidence]), json.dumps(attributes or {}), provenance, multiplicity,
             corpus))
        self.conn.commit()
        return rel_id

    def add_contradiction(self, entity_name: str, claim_a: str, claim_b: str,
                          source_a: str = "", source_b: str = "") -> str:
        """Record a contradiction about an entity."""
        entity_id = self.add_entity(entity_name)
        contra_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO contradictions (id, entity_id, claim_a, claim_b, source_a, source_b) VALUES (?, ?, ?, ?, ?, ?)",
            (contra_id, entity_id, claim_a, claim_b, source_a, source_b))
        self.conn.commit()
        return contra_id

    # --- Reasoning quality records ---

    def add_role_record(self, run_id: str, node_id: str, parent_id: str = None,
                        role_name: str = None, mission: str = None,
                        bar: str = None, heuristic: str = None,
                        scope_description: str = None, budget: float = None,
                        corpus: str = None, tree_position: str = None,
                        depth: int = None) -> str:
        """Record a role assignment for a node."""
        rec_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO role_records (id, run_id, node_id, parent_id, role_name,
               mission, bar, heuristic, scope_description, budget, corpus,
               tree_position, depth)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec_id, run_id, node_id, parent_id, role_name, mission, bar,
             heuristic, scope_description, budget, corpus, tree_position, depth))
        self.conn.commit()
        return rec_id

    def add_decision_record(self, run_id: str, node_id: str,
                            decision_type: str = None, outcome: str = None,
                            reasoning_summary: str = None) -> str:
        """Record a decision made during exploration."""
        rec_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO decision_records (id, run_id, node_id, decision_type,
               outcome, reasoning_summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rec_id, run_id, node_id, decision_type, outcome, reasoning_summary))
        self.conn.commit()
        return rec_id

    def add_outcome_record(self, run_id: str, node_id: str,
                           observation_count: int = 0,
                           budget_allocated: float = None,
                           budget_spent: float = None,
                           turn2_classification: str = None,
                           reader_test_scores: dict = None,
                           validation_outcomes: dict = None) -> str:
        """Record the outcome of a node's work."""
        rec_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO outcome_records (id, run_id, node_id, observation_count,
               budget_allocated, budget_spent, turn2_classification,
               reader_test_scores, validation_outcomes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec_id, run_id, node_id, observation_count, budget_allocated,
             budget_spent, turn2_classification,
             json.dumps(reader_test_scores or {}),
             json.dumps(validation_outcomes or {})))
        self.conn.commit()
        return rec_id

    # --- Lookups ---

    def find_entities(self, query: str, limit: int = 20) -> list[dict]:
        """Search entities by name (partial match)."""
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE name LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def get_entity_context(self, entity_name: str, depth: int = 2) -> dict:
        """Get full context for an entity: observations, relationships, contradictions."""
        entity = self.conn.execute(
            "SELECT * FROM entities WHERE name = ?", (entity_name,)).fetchone()
        if not entity:
            return {"entity": entity_name, "found": False}

        eid = entity["id"]

        observations = [dict(r) for r in self.conn.execute(
            "SELECT * FROM observations WHERE entity_id = ? ORDER BY confidence DESC",
            (eid,)).fetchall()]

        # Relationships from this entity
        rels_out = [dict(r) for r in self.conn.execute(
            """SELECT r.*, e.name as to_name FROM relationships r
               JOIN entities e ON r.to_entity = e.id
               WHERE r.from_entity = ?""", (eid,)).fetchall()]

        # Relationships to this entity
        rels_in = [dict(r) for r in self.conn.execute(
            """SELECT r.*, e.name as from_name FROM relationships r
               JOIN entities e ON r.from_entity = e.id
               WHERE r.to_entity = ?""", (eid,)).fetchall()]

        contradictions = [dict(r) for r in self.conn.execute(
            "SELECT * FROM contradictions WHERE entity_id = ?",
            (eid,)).fetchall()]

        # If depth > 1, get related entities' observations too
        related_context = {}
        if depth > 1:
            related_names = set()
            for r in rels_out:
                related_names.add(r.get("to_name", ""))
            for r in rels_in:
                related_names.add(r.get("from_name", ""))
            for name in list(related_names)[:10]:
                if name:
                    related_context[name] = self.get_entity_context(name, depth=1)

        return {
            "entity": entity_name,
            "found": True,
            "type": entity["entity_type"],
            "properties": json.loads(entity["properties"]),
            "observations": observations,
            "relationships_from": rels_out,
            "relationships_to": rels_in,
            "contradictions": contradictions,
            "related": related_context,
        }

    def traverse(self, start_entity: str, depth: int = 3) -> list[dict]:
        """Traverse the graph from a starting entity, collecting context."""
        visited = set()
        results = []

        def _traverse(name, current_depth):
            if current_depth <= 0 or name in visited:
                return
            visited.add(name)
            ctx = self.get_entity_context(name, depth=1)
            if ctx.get("found"):
                results.append(ctx)
                # Follow relationships
                for r in ctx.get("relationships_from", []):
                    _traverse(r.get("to_name", ""), current_depth - 1)
                for r in ctx.get("relationships_to", []):
                    _traverse(r.get("from_name", ""), current_depth - 1)

        _traverse(start_entity, depth)
        return results

    def stats(self) -> dict:
        """Get graph statistics."""
        entities = self.conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
        observations = self.conn.execute("SELECT COUNT(*) as c FROM observations").fetchone()["c"]
        relationships = self.conn.execute("SELECT COUNT(*) as c FROM relationships").fetchone()["c"]
        contradictions = self.conn.execute("SELECT COUNT(*) as c FROM contradictions").fetchone()["c"]
        rel_types = [r["relationship_type"] for r in self.conn.execute(
            "SELECT DISTINCT relationship_type FROM relationships").fetchall()]
        entity_types = [r["entity_type"] for r in self.conn.execute(
            "SELECT DISTINCT entity_type FROM entities").fetchall()]
        runs = set()
        for col in ("first_observed_run", "last_observed_run"):
            for r in self.conn.execute(
                    f"SELECT DISTINCT {col} FROM entities WHERE {col} IS NOT NULL").fetchall():
                runs.add(r[0])
        role_count = self.conn.execute("SELECT COUNT(*) as c FROM role_records").fetchone()["c"]
        decision_count = self.conn.execute("SELECT COUNT(*) as c FROM decision_records").fetchone()["c"]
        outcome_count = self.conn.execute("SELECT COUNT(*) as c FROM outcome_records").fetchone()["c"]
        return {
            "entities": entities,
            "observations": observations,
            "relationships": relationships,
            "contradictions": contradictions,
            "entity_types": entity_types,
            "relationship_types": rel_types,
            "runs_represented": sorted(runs),
            "role_records": role_count,
            "decision_records": decision_count,
            "outcome_records": outcome_count,
        }

    def export_json(self) -> dict:
        """Export the entire graph as JSON."""
        return {
            "entities": [dict(r) for r in self.conn.execute("SELECT * FROM entities").fetchall()],
            "observations": [dict(r) for r in self.conn.execute("SELECT * FROM observations").fetchall()],
            "relationships": [dict(r) for r in self.conn.execute("SELECT * FROM relationships").fetchall()],
            "contradictions": [dict(r) for r in self.conn.execute("SELECT * FROM contradictions").fetchall()],
        }

    def query_keyword(self, question: str) -> dict:
        """Structured graph traversal driven by a natural language question.

        Translates the question into SQL queries against the graph and returns
        structured results. Supports entity lookups, relationship traversals,
        and aggregations.
        """
        results = {}

        # Extract potential entity names from the question (words with 3+ chars)
        words = [w.strip("?.,!\"'") for w in question.split() if len(w.strip("?.,!\"'")) >= 3]

        # 1. Entity lookup — find entities matching any word in the question
        matched_entities = []
        for word in words:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE name LIKE ? OR canonical_name LIKE ? LIMIT 10",
                (f"%{word}%", f"%{word}%")).fetchall()
            for r in rows:
                entity = dict(r)
                if entity["id"] not in [e["id"] for e in matched_entities]:
                    matched_entities.append(entity)
        results["matched_entities"] = matched_entities[:20]

        # 2. If entities found, get their relationships and observations
        entity_ids = [e["id"] for e in matched_entities[:10]]
        if entity_ids:
            placeholders = ",".join("?" * len(entity_ids))

            # Observations for matched entities
            obs_rows = self.conn.execute(
                f"""SELECT o.*, e.name as entity_name FROM observations o
                    JOIN entities e ON o.entity_id = e.id
                    WHERE o.entity_id IN ({placeholders})
                    ORDER BY o.confidence DESC LIMIT 50""",
                entity_ids).fetchall()
            results["observations"] = [dict(r) for r in obs_rows]

            # Relationships involving matched entities
            rel_rows = self.conn.execute(
                f"""SELECT r.*, ef.name as from_name, et.name as to_name
                    FROM relationships r
                    JOIN entities ef ON r.from_entity = ef.id
                    JOIN entities et ON r.to_entity = et.id
                    WHERE r.from_entity IN ({placeholders}) OR r.to_entity IN ({placeholders})
                    ORDER BY r.confidence DESC LIMIT 50""",
                entity_ids + entity_ids).fetchall()
            results["relationships"] = [dict(r) for r in rel_rows]

            # Contradictions
            contra_rows = self.conn.execute(
                f"SELECT * FROM contradictions WHERE entity_id IN ({placeholders}) LIMIT 20",
                entity_ids).fetchall()
            results["contradictions"] = [dict(r) for r in contra_rows]
        else:
            results["observations"] = []
            results["relationships"] = []
            results["contradictions"] = []

        # 3. Aggregation queries for common question patterns
        q_lower = question.lower()
        if any(kw in q_lower for kw in ("how many", "count", "total")):
            results["aggregations"] = self.stats()
        if any(kw in q_lower for kw in ("most", "top", "highest", "largest")):
            top_entities = self.conn.execute(
                "SELECT name, entity_type, observation_count FROM entities ORDER BY observation_count DESC LIMIT 10"
            ).fetchall()
            results["top_entities_by_observations"] = [dict(r) for r in top_entities]

        results["question"] = question
        results["entity_count"] = len(matched_entities)
        return results

    # --- LLM-planned query ---

    def _get_schema_context(self) -> str:
        """Build schema context string for LLM query planning."""
        tables = ["entities", "observations", "relationships", "contradictions",
                  "role_records", "decision_records", "outcome_records"]
        parts = []
        for table in tables:
            # Get column info
            cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_descs = [f"  {c['name']} {c['type']}" for c in cols]
            parts.append(f"TABLE {table}:\n" + "\n".join(col_descs))
            # Get one sample row
            sample = self.conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
            if sample:
                parts.append(f"  SAMPLE: {json.dumps(dict(sample), default=str)[:500]}")

        # Available corpus values
        corpora = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT corpus FROM entities WHERE corpus IS NOT NULL").fetchall()]
        parts.append(f"\nAvailable corpus values: {corpora}")

        # Available entity_types
        etypes = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT entity_type FROM entities WHERE entity_type IS NOT NULL").fetchall()]
        parts.append(f"Available entity_types: {etypes}")

        # Available relationship_types
        rtypes = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT relationship_type FROM relationships").fetchall()]
        parts.append(f"Available relationship_types: {rtypes}")

        return "\n".join(parts)

    async def query_with_llm(self, question: str, corpus: str = None) -> dict:
        """LLM-planned graph traversal. Returns {question, sql, results, answer}."""
        client = anthropic.AsyncAnthropic()
        schema_ctx = self._get_schema_context()

        corpus_hint = ""
        if corpus:
            corpus_hint = f"\nFilter to corpus = '{corpus}' where applicable."

        # Step 1: Plan — ask LLM to write SQL
        plan_prompt = f"""You are querying a knowledge graph stored in SQLite. Here is the schema:

{schema_ctx}
{corpus_hint}

Important notes:
- entities.id is referenced by observations.entity_id, relationships.from_entity, relationships.to_entity
- observations.source_run_id links to which run produced the observation
- The 'claim' column in observations contains the text of findings
- The 'name' column in entities contains entity names (packages, maintainers, concepts, etc.)
- Use LIKE with % wildcards for partial text matching on names and claims

Write a single SQLite-compatible SQL query to answer this question:
"{question}"

Return ONLY the SQL query, no explanation. The query should return useful columns with clear aliases."""

        plan_response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": plan_prompt}],
        )
        sql = plan_response.content[0].text.strip()
        # Strip markdown code fences if present
        if sql.startswith("```"):
            sql = "\n".join(sql.split("\n")[1:])
        if sql.endswith("```"):
            sql = "\n".join(sql.split("\n")[:-1])
        sql = sql.strip()

        # Step 2: Execute
        error = None
        rows = []
        try:
            raw = self.conn.execute(sql).fetchall()
            rows = [dict(r) for r in raw]
        except Exception as e:
            error = str(e)
            # One retry: feed error back to LLM
            retry_prompt = f"""The SQL query failed. Error: {error}

Original query:
{sql}

Schema:
{schema_ctx}

Fix the SQL query. Return ONLY the corrected SQL, no explanation."""
            retry_response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            sql = retry_response.content[0].text.strip()
            if sql.startswith("```"):
                sql = "\n".join(sql.split("\n")[1:])
            if sql.endswith("```"):
                sql = "\n".join(sql.split("\n")[:-1])
            sql = sql.strip()
            try:
                raw = self.conn.execute(sql).fetchall()
                rows = [dict(r) for r in raw]
                error = None
            except Exception as e2:
                error = f"Retry also failed: {e2}"

        # Step 3: Synthesize — pass results to LLM for natural language answer
        if error:
            return {"question": question, "sql": sql, "results": [], "answer": f"Query failed: {error}"}

        # Truncate results for synthesis prompt
        results_str = json.dumps(rows[:50], default=str, indent=2)
        synth_prompt = f"""Based on these SQL query results from a knowledge graph, answer the original question.

Question: "{question}"

SQL used: {sql}

Results ({len(rows)} rows, showing first 50):
{results_str}

Provide a concise, factual answer grounded in the data. Reference specific entities and numbers from the results."""

        synth_response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": synth_prompt}],
        )
        answer = synth_response.content[0].text.strip()

        return {
            "question": question,
            "sql": sql,
            "results": rows[:50],
            "answer": answer,
        }

    # --- Reasoning quality queries ---

    def query_role_patterns(self, corpus: str = None,
                            budget_range: tuple = None) -> list[dict]:
        """What role definitions produced strong outcomes?

        Joins role_records with outcome_records to find roles whose nodes
        had high observation counts and met their bar (turn2 classification).
        """
        sql = """
            SELECT r.role_name, r.bar, r.tree_position, r.depth, r.corpus,
                   o.observation_count, o.budget_spent, o.turn2_classification,
                   o.reader_test_scores
            FROM role_records r
            JOIN outcome_records o ON r.run_id = o.run_id AND r.node_id = o.node_id
            WHERE 1=1
        """
        params = []
        if corpus:
            sql += " AND r.corpus = ?"
            params.append(corpus)
        if budget_range:
            sql += " AND r.budget >= ? AND r.budget <= ?"
            params.extend(budget_range)
        sql += " ORDER BY o.observation_count DESC LIMIT 100"
        rows = self.conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["reader_test_scores"] = json.loads(d["reader_test_scores"]) if d["reader_test_scores"] else {}
            results.append(d)
        return results

    def query_continuation_outcomes(self, classification: str = None) -> list[dict]:
        """What continuation decisions followed specific turn2 classifications?

        Shows decision records alongside their outcome classification to reveal
        whether MET_COMMITTED nodes get different continuation decisions than
        POOR_REASONING ones.
        """
        sql = """
            SELECT d.decision_type, d.outcome, d.reasoning_summary,
                   o.turn2_classification, o.observation_count, o.budget_spent,
                   r.role_name, r.bar
            FROM decision_records d
            JOIN outcome_records o ON d.run_id = o.run_id AND d.node_id = o.node_id
            LEFT JOIN role_records r ON d.run_id = r.run_id AND d.node_id = r.node_id
            WHERE d.decision_type IN ('turn2', 'continuation', 'reassessment')
        """
        params = []
        if classification:
            sql += " AND o.turn2_classification = ?"
            params.append(classification)
        sql += " ORDER BY d.created_at DESC LIMIT 100"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_recurring_entities(self, min_runs: int = 2) -> list[dict]:
        """What entities appear across multiple runs?

        Finds entities observed in at least min_runs different runs, indicating
        persistent patterns worth tracking.
        """
        rows = self.conn.execute("""
            SELECT e.name, e.entity_type, e.observation_count,
                   e.first_observed_run, e.last_observed_run,
                   COUNT(DISTINCT obs.source_run_id) as run_count
            FROM entities e
            JOIN observations obs ON e.id = obs.entity_id
            WHERE obs.source_run_id IS NOT NULL AND obs.source_run_id != ''
            GROUP BY e.id
            HAVING run_count >= ?
            ORDER BY run_count DESC, e.observation_count DESC
            LIMIT 100
        """, (min_runs,)).fetchall()
        return [dict(r) for r in rows]

    def query_findings_by_validation(self, status: str = None) -> list[dict]:
        """What findings were confirmed/weakened/refuted?

        Queries outcome_records for validation results, optionally filtered
        by a specific status string within the validation_outcomes JSON.
        """
        sql = """
            SELECT o.run_id, o.node_id, o.observation_count,
                   o.turn2_classification, o.validation_outcomes,
                   r.role_name, r.scope_description
            FROM outcome_records o
            LEFT JOIN role_records r ON o.run_id = r.run_id AND o.node_id = r.node_id
            WHERE o.validation_outcomes != '{}'
        """
        params = []
        if status:
            # SQLite JSON: search within the validation_outcomes text
            sql += " AND o.validation_outcomes LIKE ?"
            params.append(f"%{status}%")
        sql += " ORDER BY o.created_at DESC LIMIT 100"
        rows = self.conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["validation_outcomes"] = json.loads(d["validation_outcomes"]) if d["validation_outcomes"] else {}
            results.append(d)
        return results

    def close(self):
        self.conn.close()
