"""Use-case graph — per-corpus cumulative knowledge layer.

One SQLite database per corpus type (e.g., catalog/use_case_graph_npm.db).
Accumulates entities, observations, relationships, and findings across all
runs against that corpus. Supports semantic search via embeddings.

This is the Pinecone-replacement artifact: a structured knowledge layer
that grows over time and supports both vector queries and SQL queries.
"""

import json
import sqlite3
import struct
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT,
    canonical_name TEXT,
    attributes TEXT DEFAULT '{}',
    first_observed_run TEXT,
    last_observed_run TEXT,
    observation_count INTEGER DEFAULT 0,
    runs_observed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    entity_id TEXT,
    claim TEXT,
    source_node_id TEXT,
    source_run_id TEXT,
    confidence REAL,
    observation_type TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    from_entity TEXT,
    to_entity TEXT,
    relationship_type TEXT,
    confidence REAL,
    evidence TEXT,
    source_run_id TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    summary TEXT,
    tier TEXT,
    validation_status TEXT,
    significance_level TEXT,
    impact_summary TEXT,
    source_run_id TEXT
);

CREATE TABLE IF NOT EXISTS vectors (
    id TEXT PRIMARY KEY,
    source_table TEXT,
    source_id TEXT,
    text TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT,
    budget REAL,
    cost REAL,
    observations_count INTEGER,
    findings_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_obs_run ON observations(source_run_id);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(source_run_id);
"""


def update_use_case_graph(run_dir: str, run_id: str, corpus: str):
    """Update the per-corpus use-case graph with this run's data.

    Upserts entities, appends observations/findings, updates embeddings
    for new records only.
    """
    corpus_name = corpus.lower().replace("source", "").replace("registry", "")
    db_path = Path("catalog") / f"use_case_graph_{corpus_name}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    run_path = Path(run_dir)
    kg_path = run_path / "knowledge_graph.json"
    metrics_path = run_path / "metrics.json"
    report_path = run_path / "report.md"

    if not kg_path.exists():
        print(f"  [USE-CASE GRAPH] No knowledge_graph.json — skipping")
        return None

    kg = json.loads(kg_path.read_text())
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Filter to this run's data only
    run_entities = [e for e in kg.get("entities", [])
                    if e.get("last_observed_run") == run_id or e.get("first_observed_run") == run_id]
    run_observations = [o for o in kg.get("observations", [])
                        if o.get("source_run_id") == run_id]
    run_relationships = [r for r in kg.get("relationships", [])
                         if r.get("source_run_id", "") == run_id]

    # Upsert entities
    for e in run_entities:
        existing = conn.execute("SELECT id, runs_observed FROM entities WHERE canonical_name = ?",
                                (e.get("canonical_name", e["name"]),)).fetchone()
        if existing:
            conn.execute("""UPDATE entities SET last_observed_run = ?, observation_count = observation_count + ?,
                           runs_observed = runs_observed + 1, attributes = ? WHERE id = ?""",
                         (run_id, e.get("observation_count", 0), e.get("attributes", "{}"), existing["id"]))
        else:
            conn.execute("""INSERT INTO entities (id, name, entity_type, canonical_name, attributes,
                           first_observed_run, last_observed_run, observation_count, runs_observed)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                         (e["id"], e["name"], e.get("entity_type", ""),
                          e.get("canonical_name", e["name"]), e.get("attributes", "{}"),
                          run_id, run_id, e.get("observation_count", 0)))

    # Append observations (always new)
    for o in run_observations:
        conn.execute("""INSERT OR IGNORE INTO observations
                       (id, entity_id, claim, source_node_id, source_run_id, confidence, observation_type, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (o["id"], o.get("entity_id", ""), o.get("claim", ""),
                      o.get("source_node_id", ""), run_id,
                      o.get("confidence", 0.5), o.get("observation_type", ""),
                      o.get("created_at", "")))

    # Upsert relationships
    for r in run_relationships:
        conn.execute("""INSERT OR IGNORE INTO relationships
                       (id, from_entity, to_entity, relationship_type, confidence, evidence, source_run_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (r.get("id", ""), r.get("from_entity", ""), r.get("to_entity", ""),
                      r.get("relationship_type", ""), r.get("confidence", 0.5),
                      r.get("evidence", "[]"), run_id))

    # Append findings from report
    import re
    if report_path.exists():
        report_text = report_path.read_text()
        finding_pattern = re.compile(r"### Finding (\d+\.\d+):\s*(.+?)(?=\n)")
        for m in finding_pattern.finditer(report_text):
            finding_id = f"{run_id}-finding-{m.group(1)}"
            conn.execute("""INSERT OR IGNORE INTO findings
                           (id, summary, tier, validation_status, source_run_id)
                           VALUES (?, ?, ?, ?, ?)""",
                         (finding_id, m.group(2).strip(), f"Tier {m.group(1)[0]}", "validated", run_id))

    # Log this run
    conn.execute("""INSERT OR REPLACE INTO run_log (run_id, timestamp, budget, cost, observations_count, findings_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                 (run_id, metrics.get("timestamp", ""),
                  metrics.get("cost", {}).get("budget_authorized", 0),
                  metrics.get("cost", {}).get("total", 0),
                  len(run_observations),
                  conn.execute("SELECT COUNT(*) FROM findings WHERE source_run_id = ?", (run_id,)).fetchone()[0]))

    conn.commit()

    # Embed new entities/observations only
    _embed_new_records(conn, db_path)

    # Stats
    total_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    total_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    total_runs = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
    total_vectors = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    conn.close()
    print(f"  [USE-CASE GRAPH] {corpus_name}: {total_entities} entities, "
          f"{total_obs} obs, {total_runs} runs, {total_vectors} vectors → {db_path}")
    return str(db_path)


def _embed_new_records(conn: sqlite3.Connection, db_path: Path):
    """Embed entities and observations that don't yet have vectors."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return

    # Find records without embeddings
    existing_ids = set(r[0] for r in conn.execute("SELECT source_id FROM vectors").fetchall())

    texts = []
    entities = conn.execute("SELECT id, name, entity_type FROM entities").fetchall()
    for e in entities:
        if e["id"] not in existing_ids:
            text = f"{e['name']} ({e['entity_type'] or 'entity'})"
            texts.append((f"ent-{e['id']}", "entity", e["id"], text[:512]))

    observations = conn.execute("SELECT id, claim FROM observations").fetchall()
    for o in observations:
        if o["id"] not in existing_ids and o["claim"]:
            texts.append((f"obs-{o['id']}", "observation", o["id"], o["claim"][:512]))

    if not texts:
        return

    print(f"  [USE-CASE GRAPH] Embedding {len(texts)} new records...")
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    batch_texts = [t[3] for t in texts]
    embeddings = model.encode(batch_texts, show_progress_bar=False, batch_size=256)

    for i, (vec_id, source_table, source_id, text) in enumerate(texts):
        blob = struct.pack(f"{len(embeddings[i])}f", *embeddings[i])
        conn.execute("INSERT OR IGNORE INTO vectors (id, source_table, source_id, text, embedding) VALUES (?, ?, ?, ?, ?)",
                     (vec_id, source_table, source_id, text, blob))
    conn.commit()
