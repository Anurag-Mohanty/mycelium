"""Generic REST API connector — dynamically configured by LLM.

When a user asks to explore a data source we don't have a built-in
connector for, the LLM figures out the API structure and this connector
uses that configuration to fetch data. No code generation — just a
generic REST client that follows LLM-provided instructions.

Works with any public JSON REST API. No auth required.
"""

import asyncio
import httpx
from .base import DataSource


class GenericAPISource(DataSource):
    """A connector that works with any public REST API using LLM-provided config."""

    def __init__(self, config: dict):
        """Initialize with LLM-provided API configuration.

        config should have:
            base_url: "https://api.fda.gov"
            search_endpoint: "/drug/event.json"
            search_param: "search"  (query parameter name)
            limit_param: "limit"  (limit parameter name)
            max_per_request: 100
            records_path: "results"  (JSON path to the array of records)
            total_path: "meta.results.total"  (JSON path to total count, optional)
            field_mapping: {
                "id": "safetyreportid",
                "title": "patient.drug.0.medicinalproduct",
                "date": "receiptdate",
                "description": "patient.reaction.0.reactionmeddrapt",
                ...
            }
            search_terms: ["aspirin", "ibuprofen", ...]  (for broad survey)
            rate_limit_ms: 500
            source_name: "openFDA Drug Adverse Events"
        """
        self.config = config
        self.client = httpx.AsyncClient(timeout=30.0)
        self._last_call = 0.0
        self.source_name = config.get("source_name", "Generic API")

    async def _rate_limit(self):
        delay = self.config.get("rate_limit_ms", 500) / 1000
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def _get(self, url: str, params: dict = None) -> dict | None:
        await self._rate_limit()
        try:
            resp = await self.client.get(url, params=params or {})
            if resp.status_code == 200:
                return resp.json()
            return None
        except (httpx.HTTPError, Exception):
            return None

    def _extract_path(self, data: dict, path: str):
        """Navigate a dot-separated path in nested JSON. e.g. 'meta.results.total'"""
        parts = path.split(".")
        current = data
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _map_record(self, raw: dict) -> dict:
        """Map a raw API record to our standard format using field_mapping."""
        mapping = self.config.get("field_mapping", {})
        record = {}
        for our_field, their_path in mapping.items():
            val = self._extract_path(raw, their_path) if "." in their_path else raw.get(their_path)
            if val is not None:
                record[our_field] = val

        # Always include raw fields that aren't mapped (the survey engine handles any fields)
        for key, val in raw.items():
            if key not in record and not isinstance(val, (dict, list)):
                record[key] = val

        return record

    async def survey(self, filters: dict) -> dict:
        """Survey the API — fetch a sample and describe the shape."""
        records = await self.fetch(filters, max_results=50)
        return {
            "source": self.source_name,
            "total_packages": "unknown",
            "scope": filters.get("keyword", "broad survey"),
            "packages": records[:20],
            "ecosystem_shape": {
                "packages_sampled": len(records),
                "fields_found": list(records[0].keys()) if records else [],
            },
        }

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch records from the API."""
        base = self.config.get("base_url", "")
        endpoint = self.config.get("search_endpoint", "")
        search_param = self.config.get("search_param", "search")
        limit_param = self.config.get("limit_param", "limit")
        max_per = self.config.get("max_per_request", 100)
        records_path = self.config.get("records_path", "")

        keyword = filters.get("keyword", "")
        url = f"{base}{endpoint}"

        params = {}
        if keyword:
            params[search_param] = keyword
        params[limit_param] = min(max_results, max_per)

        data = await self._get(url, params)
        if not data:
            return []

        # Extract records from the response
        if records_path:
            raw_records = self._extract_path(data, records_path)
        else:
            raw_records = data if isinstance(data, list) else [data]

        if not isinstance(raw_records, list):
            raw_records = [raw_records] if raw_records else []

        return [self._map_record(r) for r in raw_records[:max_results]]

    async def fetch_bulk_metadata(self, max_records: int = 2000,
                                   progress_callback=None) -> list[dict]:
        """Fetch many records by searching across multiple terms."""
        search_terms = self.config.get("search_terms", [""])
        base = self.config.get("base_url", "")
        endpoint = self.config.get("search_endpoint", "")
        search_param = self.config.get("search_param", "search")
        limit_param = self.config.get("limit_param", "limit")
        max_per = self.config.get("max_per_request", 100)
        records_path = self.config.get("records_path", "")

        all_records = []
        seen_ids = set()
        id_field = self.config.get("field_mapping", {}).get("id", "id")

        for term in search_terms:
            if len(all_records) >= max_records:
                break

            url = f"{base}{endpoint}"
            params = {limit_param: min(max_per, max_records - len(all_records))}
            if term:
                params[search_param] = term

            data = await self._get(url, params)
            if not data:
                continue

            raw = self._extract_path(data, records_path) if records_path else data
            if not isinstance(raw, list):
                raw = [raw] if raw else []

            for r in raw:
                mapped = self._map_record(r)
                rec_id = mapped.get("id", mapped.get("title", str(len(all_records))))
                if rec_id not in seen_ids:
                    seen_ids.add(rec_id)
                    all_records.append(mapped)

            if progress_callback:
                progress_callback({
                    "fetched": len(all_records),
                    "total_estimated": max_records,
                })

        return all_records

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single record by ID — uses search with the ID as query."""
        results = await self.fetch({"keyword": doc_id}, max_results=1)
        return results[0] if results else {}

    async def close(self):
        await self.client.aclose()
