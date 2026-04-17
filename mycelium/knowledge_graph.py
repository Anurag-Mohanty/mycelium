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
        """)
        self.conn.commit()

    def add_entity(self, name: str, entity_type: str = "unknown",
                   properties: dict = None) -> str:
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
            return entity_id

        entity_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO entities (id, name, entity_type, properties) VALUES (?, ?, ?, ?)",
            (entity_id, name, entity_type, json.dumps(properties or {})))
        self.conn.commit()
        return entity_id

    def add_observation(self, entity_name: str, claim: str,
                        source_node_id: str = "", source_run_id: str = "",
                        confidence: float = 0.5, observation_type: str = "pattern",
                        entity_type: str = "unknown") -> str:
        """Add an observation about an entity."""
        entity_id = self.add_entity(entity_name, entity_type)
        obs_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO observations (id, entity_id, claim, source_node_id, source_run_id, confidence, observation_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (obs_id, entity_id, claim, source_node_id, source_run_id, confidence, observation_type))
        self.conn.commit()
        return obs_id

    def add_relationship(self, from_name: str, to_name: str,
                         relationship_type: str, confidence: float = 0.5,
                         evidence: str = "",
                         from_type: str = "unknown", to_type: str = "unknown") -> str:
        """Add a relationship between two entities."""
        from_id = self.add_entity(from_name, from_type)
        to_id = self.add_entity(to_name, to_type)

        # Check for existing relationship
        existing = self.conn.execute(
            "SELECT id, evidence FROM relationships WHERE from_entity = ? AND to_entity = ? AND relationship_type = ?",
            (from_id, to_id, relationship_type)).fetchone()

        if existing:
            # Append evidence
            old_evidence = json.loads(existing["evidence"])
            old_evidence.append(evidence)
            self.conn.execute(
                "UPDATE relationships SET evidence = ?, confidence = MAX(confidence, ?) WHERE id = ?",
                (json.dumps(old_evidence), confidence, existing["id"]))
            self.conn.commit()
            return existing["id"]

        rel_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO relationships (id, from_entity, to_entity, relationship_type, confidence, evidence) VALUES (?, ?, ?, ?, ?, ?)",
            (rel_id, from_id, to_id, relationship_type, confidence, json.dumps([evidence])))
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
        return {
            "entities": entities,
            "observations": observations,
            "relationships": relationships,
            "contradictions": contradictions,
        }

    def export_json(self) -> dict:
        """Export the entire graph as JSON."""
        return {
            "entities": [dict(r) for r in self.conn.execute("SELECT * FROM entities").fetchall()],
            "observations": [dict(r) for r in self.conn.execute("SELECT * FROM observations").fetchall()],
            "relationships": [dict(r) for r in self.conn.execute("SELECT * FROM relationships").fetchall()],
            "contradictions": [dict(r) for r in self.conn.execute("SELECT * FROM contradictions").fetchall()],
        }

    def close(self):
        self.conn.close()
