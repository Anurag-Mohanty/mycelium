"""Deliverable generation — self-contained SQLite database for a completed run.

Reads from output/{run_id}/ files (nodes/*.json, knowledge_graph.json,
metrics.json, report.md) and produces deliverable.db with entities,
observations, relationships, findings, engagement metadata, and optionally
vector embeddings for semantic search.
"""

import json
import logging
import os
import re
import sqlite3
import struct
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_deliverable(run_dir: str, run_id: str) -> str:
    """Generate deliverable.db for a completed run. Returns path to deliverable.db."""
    run_path = Path(run_dir)
    db_path = run_path / "deliverable.db"

    # Remove stale deliverable if present
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _create_tables(conn)

    # Load source data
    kg = _load_json(run_path / "knowledge_graph.json") or {}
    metrics = _load_json(run_path / "metrics.json") or {}
    nodes = _load_nodes(run_path / "nodes")
    report_text = _load_text(run_path / "report.md")

    # Populate tables
    _insert_entities(conn, kg.get("entities", []))
    _insert_observations(conn, kg.get("observations", []))
    _insert_relationships(conn, kg.get("relationships", []))
    _insert_findings(conn, report_text)
    _insert_engagement_metadata(conn, metrics, kg, nodes, run_id)

    conn.commit()

    # Embedding layer — optional
    _generate_embeddings(conn, db_path)

    conn.close()
    logger.info(f"Deliverable written to {db_path}")
    return str(db_path)


# --- Schema ---

def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT,
            canonical_name TEXT,
            attributes TEXT DEFAULT '{}',
            corpus TEXT,
            observation_count INTEGER DEFAULT 0
        );
        CREATE INDEX idx_ent_name ON entities(name);
        CREATE INDEX idx_ent_type ON entities(entity_type);

        CREATE TABLE observations (
            id TEXT PRIMARY KEY,
            claim TEXT NOT NULL,
            source_node_id TEXT,
            confidence REAL DEFAULT 0.5,
            observation_type TEXT,
            entity_id TEXT,
            FOREIGN KEY (entity_id) REFERENCES entities(id)
        );
        CREATE INDEX idx_obs_entity ON observations(entity_id);

        CREATE TABLE relationships (
            id TEXT PRIMARY KEY,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence TEXT DEFAULT '[]',
            provenance TEXT,
            FOREIGN KEY (from_entity) REFERENCES entities(id),
            FOREIGN KEY (to_entity) REFERENCES entities(id)
        );
        CREATE INDEX idx_rel_from ON relationships(from_entity);
        CREATE INDEX idx_rel_to ON relationships(to_entity);

        CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            type TEXT,
            confidence REAL,
            validation_status TEXT,
            significance_level TEXT,
            impact_summary TEXT
        );

        CREATE TABLE engagement_metadata (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            corpus TEXT,
            total_budget REAL,
            budget_spent REAL,
            run_timestamp TEXT,
            node_count INTEGER,
            observation_count INTEGER,
            coverage_report TEXT
        );

        CREATE TABLE vectors (
            id TEXT PRIMARY KEY,
            source_table TEXT,
            source_id TEXT,
            text TEXT,
            embedding BLOB
        );
    """)


# --- Data loading helpers ---

def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _load_text(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path) as f:
        return f.read()


def _load_nodes(nodes_dir: Path) -> list[dict]:
    if not nodes_dir.is_dir():
        return []
    nodes = []
    for p in sorted(nodes_dir.glob("*.json")):
        with open(p) as f:
            nodes.append(json.load(f))
    return nodes


# --- Table population ---

def _insert_entities(conn: sqlite3.Connection, entities: list[dict]):
    for e in entities:
        conn.execute(
            """INSERT OR IGNORE INTO entities
               (id, name, entity_type, canonical_name, attributes, corpus, observation_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                e.get("id", str(uuid.uuid4())[:12]),
                e.get("name", ""),
                e.get("entity_type"),
                e.get("canonical_name") or e.get("name", ""),
                e.get("attributes") or e.get("properties", "{}"),
                e.get("corpus"),
                e.get("observation_count", 0),
            ),
        )


def _insert_observations(conn: sqlite3.Connection, observations: list[dict]):
    for o in observations:
        conn.execute(
            """INSERT OR IGNORE INTO observations
               (id, claim, source_node_id, confidence, observation_type, entity_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                o.get("id", str(uuid.uuid4())[:12]),
                o.get("claim", ""),
                o.get("source_node_id"),
                o.get("confidence", 0.5),
                o.get("observation_type"),
                o.get("entity_id"),
            ),
        )


def _insert_relationships(conn: sqlite3.Connection, relationships: list[dict]):
    for r in relationships:
        conn.execute(
            """INSERT OR IGNORE INTO relationships
               (id, from_entity, to_entity, relationship_type, confidence, evidence, provenance)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                r.get("id", str(uuid.uuid4())[:12]),
                r.get("from_entity", ""),
                r.get("to_entity", ""),
                r.get("relationship_type", ""),
                r.get("confidence", 0.5),
                r.get("evidence", "[]"),
                r.get("provenance"),
            ),
        )


def _insert_findings(conn: sqlite3.Connection, report_text: str):
    """Parse findings from report.md Tier 3+ sections."""
    if not report_text:
        return

    # Match Tier headers and their findings
    # Pattern: ### Finding X.Y: <title>
    finding_pattern = re.compile(
        r"### Finding (\d+\.\d+):\s*(.+?)(?=\n)"
    )
    # Also match tier headers for significance level
    tier_pattern = re.compile(
        r"## (Tier \d+)\s*[—–-]\s*(.+?)(?=\n)"
    )

    # Build tier map: figure out which tier each line range belongs to
    tier_ranges = []
    for m in tier_pattern.finditer(report_text):
        tier_ranges.append((m.start(), m.group(1), m.group(2).strip()))

    def _tier_for_pos(pos: int) -> tuple[str, str]:
        current_tier = ("Unknown", "")
        for start, tier, desc in tier_ranges:
            if start <= pos:
                current_tier = (tier, desc)
        return current_tier

    for m in finding_pattern.finditer(report_text):
        finding_num = m.group(1)
        title = m.group(2).strip()
        tier, tier_desc = _tier_for_pos(m.start())

        # Extract the block after this finding header until next ### or ##
        block_start = m.end()
        next_header = re.search(r"\n#{2,3} ", report_text[block_start:])
        block_end = block_start + next_header.start() if next_header else len(report_text)
        block = report_text[block_start:block_end]

        # Extract validation status
        validation = None
        val_match = re.search(r"\*\*Validation:\*\*\s*[⚠✓✗]?\s*(\w+)", block)
        if val_match:
            validation = val_match.group(1)

        # Extract impact summary
        impact = None
        impact_match = re.search(r"\*\*Impact:\*\*\s*\n(.+?)(?=\n\n|\n###|\n##|$)", block, re.DOTALL)
        if impact_match:
            impact = impact_match.group(1).strip()[:500]

        conn.execute(
            """INSERT INTO findings
               (id, summary, type, confidence, validation_status, significance_level, impact_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"finding-{finding_num}",
                title,
                tier_desc,
                None,
                validation,
                tier,
                impact,
            ),
        )


def _insert_engagement_metadata(conn: sqlite3.Connection, metrics: dict,
                                 kg: dict, nodes: list[dict], run_id: str):
    cost = metrics.get("cost", {})
    source = metrics.get("source", "")
    coverage = metrics.get("data_coverage", {})
    quality = metrics.get("quality", {})

    conn.execute(
        """INSERT INTO engagement_metadata
           (id, corpus, total_budget, budget_spent, run_timestamp,
            node_count, observation_count, coverage_report)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source,
            cost.get("budget_authorized"),
            cost.get("total"),
            metrics.get("timestamp", ""),
            len(nodes),
            len(kg.get("observations", [])),
            json.dumps(coverage, default=str),
        ),
    )


# --- Embedding layer ---

def _generate_embeddings(conn: sqlite3.Connection, db_path: Path):
    """Generate embeddings for entities and observations using OpenAI."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — skipping embeddings")
        return

    try:
        import openai
    except ImportError:
        logger.warning("openai package not installed — skipping embeddings")
        return

    client = openai.OpenAI(api_key=api_key)

    # Collect texts to embed
    texts = []  # (id, source_table, source_id, text)

    # Entities: name + type + attributes + concatenated observation claims (first 500 tokens ~= 2000 chars)
    entities = conn.execute("SELECT id, name, entity_type, attributes FROM entities").fetchall()
    for e in entities:
        eid = e["id"]
        parts = [e["name"] or ""]
        if e["entity_type"]:
            parts.append(f"({e['entity_type']})")
        attrs = e["attributes"]
        if attrs and attrs != "{}":
            parts.append(attrs[:200])
        # Get observation claims for this entity
        obs = conn.execute(
            "SELECT claim FROM observations WHERE entity_id = ? LIMIT 10", (eid,)
        ).fetchall()
        claims = " ".join(o["claim"] for o in obs if o["claim"])
        if claims:
            parts.append(claims[:2000])
        text = " ".join(parts)[:2000]  # ~500 tokens
        if text.strip():
            texts.append((f"ent-{eid}", "entity", eid, text))

    # Observations: embed claim directly
    observations = conn.execute("SELECT id, claim FROM observations").fetchall()
    for o in observations:
        if o["claim"] and o["claim"].strip():
            texts.append((f"obs-{o['id']}", "observation", o["id"], o["claim"][:2000]))

    if not texts:
        return

    # Batch embed (OpenAI allows up to 2048 texts per call)
    BATCH_SIZE = 512
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_texts = [t[3] for t in batch]
        try:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=batch_texts,
            )
        except Exception as e:
            logger.warning(f"Embedding API call failed: {e}")
            return

        for j, emb_data in enumerate(response.data):
            vec_id, source_table, source_id, text = batch[j]
            embedding = emb_data.embedding
            # Store as raw float32 bytes
            blob = struct.pack(f"{len(embedding)}f", *embedding)
            conn.execute(
                "INSERT OR IGNORE INTO vectors (id, source_table, source_id, text, embedding) VALUES (?, ?, ?, ?, ?)",
                (vec_id, source_table, source_id, text, blob),
            )

    conn.commit()
    logger.info(f"Embedded {len(texts)} texts into vectors table")


# --- Semantic query ---

def query_semantic(db_path: str, query_text: str, k: int = 10) -> list[dict]:
    """Embed query text and return top-k similar entities/observations.

    Requires OPENAI_API_KEY. Returns empty list if unavailable.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return []

    try:
        import openai
    except ImportError:
        return []

    client = openai.OpenAI(api_key=api_key)
    try:
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=[query_text],
        )
    except Exception:
        return []

    query_vec = response.data[0].embedding

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, source_table, source_id, text, embedding FROM vectors").fetchall()

    results = []
    for row in rows:
        blob = row["embedding"]
        dim = len(blob) // 4
        stored_vec = struct.unpack(f"{dim}f", blob)
        sim = _cosine_similarity(query_vec, stored_vec)
        results.append({
            "id": row["id"],
            "source_table": row["source_table"],
            "source_id": row["source_id"],
            "text": row["text"],
            "similarity": sim,
        })

    conn.close()
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:k]


def _cosine_similarity(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
