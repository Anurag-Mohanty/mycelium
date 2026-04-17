"""Federal Register API connector.

The Federal Register API is free, requires no API key, and provides access to
all federal regulatory documents: rules, proposed rules, notices, and
presidential documents.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
Rate limit: be polite — 1 second between calls.
"""

import asyncio
import httpx
from .base import DataSource

BASE_URL = "https://www.federalregister.gov/api/v1"

# All available agencies are too many to list; these are the most active
# in enforcement and rulemaking. The API returns agency slugs, not these names.
MAJOR_AGENCIES = [
    "consumer-financial-protection-bureau",
    "environmental-protection-agency",
    "federal-communications-commission",
    "federal-trade-commission",
    "food-and-drug-administration",
    "securities-and-exchange-commission",
    "small-business-administration",
    "department-of-justice",
    "department-of-the-treasury",
    "department-of-labor",
    "department-of-health-and-human-services",
    "department-of-homeland-security",
]

DOCUMENT_TYPES = ["RULE", "PRORULE", "NOTICE", "PRESDOCU"]

# Fields to request when fetching document lists (keeps responses small)
LIST_FIELDS = [
    "document_number",
    "title",
    "type",
    "abstract",
    "agencies",
    "publication_date",
    "html_url",
    "action",
    "dates",
    "docket_ids",
    "regulation_id_numbers",
    "page_length",
]


class FederalRegisterSource(DataSource):
    """Connector for the Federal Register API."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self._last_call = 0.0

    async def _rate_limit(self):
        """Enforce 1-second delay between API calls."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make a rate-limited GET request to the Federal Register API."""
        await self._rate_limit()
        url = f"{BASE_URL}{endpoint}"
        resp = await self.client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def _build_params(self, filters: dict) -> dict:
        """Convert our filter format to Federal Register API parameters."""
        params = {"per_page": 100, "order": "relevance"}

        if "agencies" in filters:
            params["conditions[agencies][]"] = filters["agencies"]
        if "document_types" in filters:
            params["conditions[type][]"] = [
                t.upper() for t in filters["document_types"]
            ]
        if "date_range" in filters and len(filters["date_range"]) == 2:
            params["conditions[publication_date][gte]"] = filters["date_range"][0]
            params["conditions[publication_date][lte]"] = filters["date_range"][1]
        if "keyword" in filters:
            params["conditions[term]"] = filters["keyword"]

        params["fields[]"] = LIST_FIELDS
        return params

    async def survey(self, filters: dict) -> dict:
        """Get structural overview: counts by agency and document type.

        This is the "shelf labels" scan. We query facet counts to understand
        the shape of the space without fetching actual documents.
        """
        survey_data = {
            "total_documents": 0,
            "by_type": {},
            "by_agency": {},
            "date_range_covered": "",
            "sample_titles": [],
        }

        # Get total count and breakdown with the given filters
        params = self._build_params(filters)
        params["per_page"] = 20  # enough samples to see the shape

        try:
            result = await self._get("/documents.json", params)
            survey_data["total_documents"] = result.get("count", 0)

            # Collect sample titles for orientation
            for doc in result.get("results", []):
                survey_data["sample_titles"].append({
                    "title": doc.get("title", ""),
                    "type": doc.get("type", ""),
                    "date": doc.get("publication_date", ""),
                    "agency": _extract_agency(doc),
                })
        except httpx.HTTPError as e:
            survey_data["error"] = f"API error during initial survey: {e}"
            return survey_data

        # Get counts by document type (if not already filtered to one type)
        if "document_types" not in filters or len(filters.get("document_types", [])) > 1:
            for doc_type in DOCUMENT_TYPES:
                type_params = self._build_params(filters)
                type_params["conditions[type][]"] = [doc_type]
                type_params["per_page"] = 1
                try:
                    result = await self._get("/documents.json", type_params)
                    type_name = {
                        "RULE": "Final Rules",
                        "PRORULE": "Proposed Rules",
                        "NOTICE": "Notices",
                        "PRESDOCU": "Presidential Documents",
                    }.get(doc_type, doc_type)
                    survey_data["by_type"][type_name] = result.get("count", 0)
                except httpx.HTTPError:
                    continue

        # If exploring broadly, get counts for major agencies
        if "agencies" not in filters:
            for agency_slug in MAJOR_AGENCIES[:6]:  # top 6 to limit API calls
                agency_params = self._build_params(filters)
                agency_params["conditions[agencies][]"] = [agency_slug]
                agency_params["per_page"] = 1
                try:
                    result = await self._get("/documents.json", agency_params)
                    count = result.get("count", 0)
                    if count > 0:
                        survey_data["by_agency"][agency_slug] = count
                except httpx.HTTPError:
                    continue

        return survey_data

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch document metadata matching filters.

        Returns documents with title, abstract, agency, date, type, and URL.
        Paginates if needed to reach max_results.
        """
        params = self._build_params(filters)
        params["per_page"] = min(max_results, 100)

        documents = []
        page = 1

        while len(documents) < max_results:
            params["page"] = page
            try:
                result = await self._get("/documents.json", params)
            except httpx.HTTPError as e:
                print(f"  [FR API] Error on page {page}: {e}")
                break

            for doc in result.get("results", []):
                documents.append({
                    "id": doc.get("document_number", ""),
                    "title": doc.get("title", ""),
                    "type": doc.get("type", ""),
                    "abstract": doc.get("abstract", "") or "",
                    "agency": _extract_agency(doc),
                    "date": doc.get("publication_date", ""),
                    "url": doc.get("html_url", ""),
                    "action": doc.get("action", ""),
                    "docket_ids": doc.get("docket_ids", []),
                    "page_length": doc.get("page_length", 0),
                })

            # Check if there are more pages
            next_url = result.get("next_page_url")
            if not next_url or len(result.get("results", [])) == 0:
                break
            page += 1

        return documents[:max_results]

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single document's full details by document number."""
        try:
            result = await self._get(f"/documents/{doc_id}.json")
            return {
                "id": result.get("document_number", doc_id),
                "title": result.get("title", ""),
                "type": result.get("type", ""),
                "abstract": result.get("abstract", "") or "",
                "body": result.get("body_html_url", ""),
                "agency": _extract_agency(result),
                "date": result.get("publication_date", ""),
                "url": result.get("html_url", ""),
                "full_text_url": result.get("raw_text_url", ""),
                "action": result.get("action", ""),
                "cfr_references": result.get("cfr_references", []),
                "docket_ids": result.get("docket_ids", []),
            }
        except httpx.HTTPError as e:
            return {"id": doc_id, "error": str(e)}

    async def close(self):
        await self.client.aclose()


def _extract_agency(doc: dict) -> str:
    """Pull the primary agency name from a document's agency list."""
    agencies = doc.get("agencies", [])
    if agencies and isinstance(agencies, list):
        first = agencies[0]
        if isinstance(first, dict):
            return first.get("name", first.get("raw_name", "Unknown"))
        return str(first)
    return "Unknown"
