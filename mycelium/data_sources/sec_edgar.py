"""SEC EDGAR connector.

The SEC's EDGAR system is public, no auth required (just User-Agent header).
Full coverage of every filing by every US public company.

APIs:
  Quarterly index:   https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/company.idx
  Submissions:       https://data.sec.gov/submissions/CIK{cik}.json
  Filing documents:  https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}
  Full-text search:  https://efts.sec.gov/LATEST/search-index?q=...

Rate limit: SEC requires 10 req/sec max with User-Agent header.
"""

import asyncio
import re
import httpx
from .base import DataSource

USER_AGENT = "Mycelium research@example.com"
SEC_BASE = "https://www.sec.gov"
DATA_SEC = "https://data.sec.gov"
INDEX_URL = f"{SEC_BASE}/Archives/edgar/full-index"


class SecEdgarSource(DataSource):
    """Connector for SEC EDGAR filings."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": USER_AGENT},
        )
        self._last_call = 0.0
        self._index_cache = []  # cached quarterly index data
        self.source_name = "SEC EDGAR"

    async def _rate_limit(self):
        """100ms between API calls (10 req/sec SEC limit)."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < 0.1:
            await asyncio.sleep(0.1 - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def _get(self, url: str) -> httpx.Response | None:
        """Rate-limited GET request."""
        await self._rate_limit()
        try:
            resp = await self.client.get(url)
            if resp.status_code == 200:
                return resp
            return None
        except (httpx.HTTPError, Exception):
            return None

    # =================================================================
    # DataSource interface
    # =================================================================

    async def survey(self, filters: dict) -> dict:
        """Get ecosystem shape — filing metadata from quarterly indices."""
        years = filters.get("years", [2024, 2025])
        form_type = filters.get("form_type", "10-K")
        keyword = filters.get("keyword", "")

        filings = await self._fetch_index(years, form_type)

        if keyword:
            filings = [f for f in filings if keyword.lower() in f.get("company", "").lower()]

        return {
            "source": "sec_edgar",
            "total_filings": len(filings),
            "scope": f"{form_type} filings from {min(years)}-{max(years)}",
            "form_type": form_type,
            "filings": filings[:200],  # sample for genesis
        }

    async def fetch(self, filters: dict, max_results: int = 50) -> list[dict]:
        """Fetch filing records matching filters."""
        keyword = filters.get("keyword", "")
        form_type = filters.get("form_type", "10-K")
        years = filters.get("years", [2023, 2024, 2025])
        companies = filters.get("companies", [])
        sic = filters.get("sic", "")

        # Use cached index if available, otherwise fetch
        if not self._index_cache:
            self._index_cache = await self._fetch_index(years, form_type)

        results = self._index_cache

        # Filter
        if keyword:
            results = [f for f in results if keyword.lower() in f.get("company", "").lower()
                       or keyword.lower() in f.get("form_type", "").lower()]
        if companies:
            company_set = {c.lower() for c in companies}
            results = [f for f in results if f.get("company", "").lower() in company_set]
        if sic:
            results = [f for f in results if f.get("sic", "").startswith(sic)]

        return results[:max_results]

    async def fetch_document(self, doc_id: str) -> dict:
        """Fetch a single filing's metadata by accession number."""
        # doc_id format: "CIK/accession"
        parts = doc_id.split("/")
        if len(parts) != 2:
            return {"id": doc_id, "error": "invalid format, expected CIK/accession"}

        cik, accession = parts
        submissions = await self._get_submissions(cik)
        if not submissions:
            return {"id": doc_id, "error": "not found"}

        return submissions

    async def fetch_bulk_metadata(self, max_records: int = 20000,
                                   progress_callback=None) -> list[dict]:
        """Fetch all 10-K filing metadata from quarterly indices."""
        years = list(range(2021, 2027))
        all_filings = []

        total_quarters = len(years) * 4
        fetched_quarters = 0

        for year in years:
            for quarter in range(1, 5):
                filings = await self._fetch_quarter(year, quarter, "10-K")
                all_filings.extend(filings)
                fetched_quarters += 1

                if progress_callback:
                    progress_callback({
                        "fetched": len(all_filings),
                        "total_estimated": max_records,
                        "quarter": f"{year}/Q{quarter}",
                        "quarters_done": fetched_quarters,
                        "quarters_total": total_quarters,
                    })

                if len(all_filings) >= max_records:
                    break
            if len(all_filings) >= max_records:
                break

        self._index_cache = all_filings
        return all_filings[:max_records]

    async def close(self):
        await self.client.aclose()

    # =================================================================
    # Index fetching
    # =================================================================

    async def _fetch_index(self, years: list[int], form_type: str = "10-K") -> list[dict]:
        """Fetch quarterly indices for given years, filter to form type."""
        all_filings = []
        for year in years:
            for quarter in range(1, 5):
                filings = await self._fetch_quarter(year, quarter, form_type)
                all_filings.extend(filings)
        return all_filings

    async def _fetch_quarter(self, year: int, quarter: int,
                              form_type: str = "10-K") -> list[dict]:
        """Fetch and parse one quarterly index file."""
        url = f"{INDEX_URL}/{year}/QTR{quarter}/company.idx"
        resp = await self._get(url)
        if not resp:
            return []

        filings = []
        lines = resp.text.split("\n")

        # Skip header lines (first 10-11 lines)
        data_started = False
        for line in lines:
            if line.startswith("---"):
                data_started = True
                continue
            if not data_started:
                continue
            if not line.strip():
                continue

            # Parse fixed-width format
            # Company Name (62 chars) | Form Type (12) | CIK (12) | Date Filed (12) | Filename
            try:
                company = line[:62].strip()
                form = line[62:74].strip()
                cik = line[74:86].strip()
                date_filed = line[86:98].strip()
                filename = line[98:].strip()
            except IndexError:
                continue

            if form_type and form != form_type:
                continue

            # Extract accession number from filename
            accession = ""
            if filename:
                parts = filename.split("/")
                if len(parts) >= 4:
                    accession = parts[3].replace(".txt", "")

            filings.append({
                "id": f"{cik}/{accession}" if accession else cik,
                "title": f"{company} — {form} ({date_filed})",
                "type": "sec_filing",
                "company": company,
                "form_type": form,
                "cik": cik,
                "date": date_filed,
                "quarter": f"{year}Q{quarter}",
                "year": year,
                "filename": filename,
                "accession": accession,
                "url": f"{SEC_BASE}/Archives/{filename}" if filename else "",
            })

        return filings

    # =================================================================
    # Company submissions
    # =================================================================

    async def _get_submissions(self, cik: str) -> dict | None:
        """Fetch company submissions metadata."""
        padded_cik = cik.zfill(10)
        resp = await self._get(f"{DATA_SEC}/submissions/CIK{padded_cik}.json")
        if not resp:
            return None
        return resp.json()

    # =================================================================
    # Risk factor extraction
    # =================================================================

    async def fetch_risk_factors(self, cik: str, accession: str,
                                  primary_doc: str) -> str | None:
        """Fetch a 10-K filing and extract the Risk Factors section."""
        # Build URL
        accession_path = accession.replace("-", "")
        url = f"{SEC_BASE}/Archives/edgar/data/{cik}/{accession_path}/{primary_doc}"

        resp = await self._get(url)
        if not resp:
            return None

        html = resp.text
        return _extract_risk_factors(html)


def _extract_risk_factors(html: str) -> str | None:
    """Extract Risk Factors section from 10-K HTML.

    Risk Factors is Item 1A, typically between "Item 1A" and "Item 1B"
    or "Item 2". Uses regex on stripped text — no BeautifulSoup needed.
    """
    # Strip HTML tags for text extraction
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    # Find "Item 1A" marker
    patterns = [
        r'Item\s+1A[\.\s\-—]+\s*Risk\s+Factors',
        r'ITEM\s+1A[\.\s\-—]+\s*RISK\s+FACTORS',
        r'Item\s+1A\b',
        r'ITEM\s+1A\b',
    ]

    start_pos = None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start_pos = match.start()
            break

    if start_pos is None:
        return None

    # Find the end — "Item 1B" or "Item 2"
    end_patterns = [
        r'Item\s+1B[\.\s\-—]+',
        r'ITEM\s+1B[\.\s\-—]+',
        r'Item\s+2[\.\s\-—]+\s*Properties',
        r'ITEM\s+2[\.\s\-—]+\s*PROPERTIES',
    ]

    end_pos = len(text)
    for pattern in end_patterns:
        match = re.search(pattern, text[start_pos + 50:], re.IGNORECASE)
        if match:
            candidate = start_pos + 50 + match.start()
            if candidate < end_pos:
                end_pos = candidate
            break

    risk_text = text[start_pos:end_pos].strip()

    # Cap at 50K chars to avoid massive sections
    if len(risk_text) > 50000:
        risk_text = risk_text[:50000] + "... [truncated]"

    return risk_text
