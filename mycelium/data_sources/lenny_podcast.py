"""Lenny's Podcast transcript connector.

Flat catalog of 319 podcast episode transcripts. Each record is one episode
with guest name, word count, duration, and transcript text.

Data source: local JSONL catalog built from transcript files.
Full transcripts served via fetch_document() for deep reading.
"""

import json
import re
from pathlib import Path
from .base import DataSource

TRANSCRIPT_DIR = Path("/tmp/lenny_data")
# Workers get fewer records but with full transcripts attached
MAX_RECORDS_WITH_CONTENT = 15


class LennyPodcastSource(DataSource):
    """Connector for Lenny's Podcast transcripts."""

    def __init__(self):
        super().__init__()

    def catalog_path(self) -> Path | None:
        return Path("catalog/lenny_enriched.jsonl")

    async def survey(self, filters: dict) -> dict:
        """Return corpus shape."""
        self._ensure_catalog_db()
        total = self._catalog_db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {
            "total_packages": total,
            "description": "Lenny's Podcast episode transcripts",
            "source": "lenny_podcast",
        }

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch records from catalog."""
        self._ensure_catalog_db()
        if not filters:
            # No filters = return all (up to max_results)
            rows = self._catalog_db.execute(
                f"SELECT * FROM records LIMIT {max_results}"
            ).fetchall()
            return [dict(r) for r in rows]
        return self.query_catalog(filters, max_results=max_results)

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch full transcript for a given episode (by guest name)."""
        # Try exact file match
        path = TRANSCRIPT_DIR / f"{doc_id}.txt"
        if not path.exists():
            # Try fuzzy match
            for p in TRANSCRIPT_DIR.glob("*.txt"):
                if doc_id.lower() in p.stem.lower():
                    path = p
                    break
        if not path.exists():
            return {"name": doc_id, "error": "transcript not found"}

        text = path.read_text(errors="replace")
        return {
            "name": path.stem,
            "full_transcript": text,
            "word_count": len(text.split()),
        }

    async def fetch_bulk_metadata(self, max_records: int = 50000,
                                   progress_callback=None) -> list[dict]:
        """Return catalog records for survey (excludes full transcript to keep survey fast)."""
        path = self.catalog_path()
        if not path or not path.exists():
            return []
        records = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    # Exclude full transcript from survey — survey needs metadata only.
                    # Full transcripts are in the catalog DB for worker access.
                    r.pop("transcript", None)
                    records.append(r)
        if progress_callback:
            progress_callback({"fetched": len(records), "total_estimated": len(records)})
        return records

    def catalog_metadata(self) -> dict:
        """Return metadata about the catalog for the engagement lead."""
        self._ensure_catalog_db()
        total = self._catalog_db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        cursor = self._catalog_db.execute("PRAGMA table_info(records)")
        fields = [{"name": row[1], "type": row[2]} for row in cursor.fetchall()]
        return {
            "total_records": total,
            "fields": fields,
            "description": "Lenny's Podcast — 319 episode transcripts with guest metadata",
        }

    def filter_schema(self) -> dict:
        return {
            "keyword": {
                "type": "string",
                "description": "Search guest names or intro text",
                "example": "growth",
                "required": False,
            },
        }

    def valid_filter_params(self) -> set[str]:
        base = set(self.filter_schema().keys())
        base.update({"name", "guest_names", "word_count", "duration_minutes",
                     "lenny_turns", "guest_turns", "multi_guest"})
        return base

    async def close(self):
        pass
