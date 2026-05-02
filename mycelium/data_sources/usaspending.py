"""USAspending API connector.

USAspending.gov provides public data on federal spending: contracts, grants,
loans, and other financial assistance. The API is free, no key required.

API docs: https://api.usaspending.gov
Rate limit: be polite — 0.5s between calls.
"""

import asyncio
import json
from pathlib import Path as _Path
import httpx
from .base import DataSource

BASE_URL = "https://api.usaspending.gov/api/v2"


class USAspendingSource(DataSource):
    """Connector for USAspending federal contract data."""

    def __init__(self):
        super().__init__()
        self.client = httpx.AsyncClient(timeout=30.0)
        self._last_call = 0.0
        self.source_name = "USAspending"

    async def _rate_limit(self):
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < 0.5:
            await asyncio.sleep(0.5 - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def _post(self, endpoint: str, payload: dict) -> dict:
        await self._rate_limit()
        url = f"{BASE_URL}{endpoint}"
        resp = await self.client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _get(self, endpoint: str, params: dict = None) -> dict:
        await self._rate_limit()
        url = f"{BASE_URL}{endpoint}"
        resp = await self.client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def catalog_path(self) -> _Path | None:
        p = _Path("catalog/usaspending_enriched.jsonl")
        return p if p.exists() and p.stat().st_size > 1000 else None

    async def survey(self, filters: dict) -> dict:
        """Get structural overview of federal spending data."""
        survey_data = {
            "total_documents": 0,
            "by_type": {},
            "by_agency": {},
            "sample_titles": [],
        }

        # Get recent awards
        payload = {
            "filters": {"time_period": [{"start_date": "2024-01-01", "end_date": "2026-12-31"}]},
            "limit": 10,
            "page": 1,
        }
        if "keyword" in filters:
            payload["filters"]["keywords"] = [filters["keyword"]]

        try:
            result = await self._post("/search/spending_by_award/", payload)
            survey_data["total_documents"] = result.get("page_metadata", {}).get("total", 0)
            for award in result.get("results", []):
                survey_data["sample_titles"].append({
                    "title": award.get("Award Description", "")[:100],
                    "type": award.get("Award Type", ""),
                    "date": award.get("Start Date", ""),
                    "agency": award.get("Awarding Agency", ""),
                })
        except httpx.HTTPError as e:
            survey_data["error"] = str(e)

        return survey_data

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch contract award records."""
        payload = {
            "filters": {"time_period": [{"start_date": "2024-01-01", "end_date": "2026-12-31"}]},
            "fields": [
                "Award ID", "Award Description", "Award Type",
                "Awarding Agency", "Awarding Sub Agency",
                "Recipient Name", "Start Date", "End Date",
                "Award Amount", "Total Outlays",
                "Contract Award Type", "NAICS Code", "NAICS Description",
                "Place of Performance City", "Place of Performance State",
            ],
            "limit": min(max_results, 100),
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
        }
        if "keyword" in filters:
            payload["filters"]["keywords"] = [filters["keyword"]]
        if "agencies" in filters:
            payload["filters"]["agencies"] = [
                {"type": "awarding", "tier": "toptier", "name": a}
                for a in (filters["agencies"] if isinstance(filters["agencies"], list)
                          else [filters["agencies"]])
            ]

        documents = []
        try:
            result = await self._post("/search/spending_by_award/", payload)
            for award in result.get("results", []):
                documents.append({
                    "id": award.get("Award ID", ""),
                    "title": award.get("Award Description", "")[:200],
                    "type": award.get("Award Type", ""),
                    "agency": award.get("Awarding Agency", ""),
                    "sub_agency": award.get("Awarding Sub Agency", ""),
                    "recipient": award.get("Recipient Name", ""),
                    "date": award.get("Start Date", ""),
                    "end_date": award.get("End Date", ""),
                    "amount": award.get("Award Amount", 0),
                    "outlays": award.get("Total Outlays", 0),
                    "naics_code": award.get("NAICS Code", ""),
                    "naics_description": award.get("NAICS Description", ""),
                    "city": award.get("Place of Performance City", ""),
                    "state": award.get("Place of Performance State", ""),
                })
        except httpx.HTTPError as e:
            print(f"  [USASPENDING] Error: {e}")

        return documents[:max_results]

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single award's details."""
        try:
            result = await self._get(f"/awards/{doc_id}/")
            return result
        except httpx.HTTPError as e:
            return {"id": doc_id, "error": str(e)}

    async def fetch_bulk_metadata(self, max_records: int = 2000,
                                   progress_callback=None) -> list[dict]:
        """Fetch contract awards and cache to JSONL.

        Fetches recent high-value contracts across agencies.
        """
        cache_path = _Path("catalog/usaspending_enriched.jsonl")
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            print(f"  [CATALOG] Loading cached enrichment from {cache_path}...")
            records = []
            with open(cache_path) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
            if len(records) >= 100:
                print(f"  [CATALOG] Loaded {len(records)} awards (cached)")
                return records

        print(f"  [CATALOG] Fetching up to {max_records} USAspending awards...")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        page = 1

        while len(records) < max_records:
            payload = {
                "filters": {
                    "time_period": [{"start_date": "2023-01-01", "end_date": "2026-12-31"}],
                    "award_type_codes": ["A", "B", "C", "D"],  # contracts
                },
                "fields": [
                    "Award ID", "Award Description", "Award Type",
                    "Awarding Agency", "Awarding Sub Agency",
                    "Recipient Name", "Start Date", "End Date",
                    "Award Amount", "Total Outlays",
                    "Contract Award Type", "NAICS Code", "NAICS Description",
                    "Place of Performance City", "Place of Performance State",
                ],
                "limit": 100,
                "page": page,
                "sort": "Award Amount",
                "order": "desc",
            }
            try:
                result = await self._post("/search/spending_by_award/", payload)
            except httpx.HTTPError as e:
                print(f"  [USASPENDING] Error on page {page}: {e}")
                break

            for award in result.get("results", []):
                record = {
                    "id": award.get("Award ID", ""),
                    "title": (award.get("Award Description", "") or "")[:200],
                    "type": award.get("Award Type", ""),
                    "agency": award.get("Awarding Agency", ""),
                    "sub_agency": award.get("Awarding Sub Agency", ""),
                    "recipient": award.get("Recipient Name", ""),
                    "date": award.get("Start Date", ""),
                    "end_date": award.get("End Date", ""),
                    "amount": award.get("Award Amount", 0),
                    "outlays": award.get("Total Outlays", 0),
                    "naics_code": award.get("NAICS Code", ""),
                    "naics_description": award.get("NAICS Description", ""),
                    "city": award.get("Place of Performance City", ""),
                    "state": award.get("Place of Performance State", ""),
                }
                records.append(record)

            if progress_callback:
                progress_callback({"fetched": len(records), "total_estimated": max_records})

            if not result.get("results") or len(result.get("results", [])) == 0:
                break
            page += 1

        with open(cache_path, "w") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")
        print(f"  [CATALOG] Cached {len(records)} awards to {cache_path}")
        return records

    async def close(self):
        await self.client.aclose()
