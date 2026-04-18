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
        """Fetch filing records with actual content.

        Always enriches with real filing data: fetches the 10-K HTML and
        extracts Risk Factors text. The agent gets content to reason about,
        not just index metadata.
        """
        keyword = filters.get("keyword", "")
        form_type = filters.get("form_type", "10-K")
        years = filters.get("years", [2021, 2022, 2023, 2024, 2025])
        companies = filters.get("companies", [])
        sic = filters.get("sic", "")

        # If specific companies requested, fetch their filings directly
        if companies:
            return await self._fetch_company_filings(companies, years, max_results)

        # Otherwise, find matching companies from the index and fetch content
        if not self._index_cache:
            self._index_cache = await self._fetch_index(years, form_type)

        # Filter index to matching filings
        matches = self._index_cache
        if keyword:
            raw_terms = keyword.replace(" OR ", " ").split()
            include = [t.lower() for t in raw_terms if not t.startswith("-")]
            exclude = [t[1:].lower() for t in raw_terms if t.startswith("-")]

            def _matches(filing):
                text = (filing.get("company", "") + " " + filing.get("title", "")).lower()
                if exclude and any(ex in text for ex in exclude):
                    return False
                return any(inc in text for inc in include) if include else True

            matches = [f for f in matches if _matches(f)]
        if sic:
            matches = [f for f in matches if f.get("sic", "").startswith(sic)]

        if not matches:
            return []

        # Get unique company names from matches, fetch their actual filings
        seen = set()
        unique_companies = []
        for m in matches:
            name = m.get("company", "")
            if name and name not in seen:
                seen.add(name)
                unique_companies.append(name)
            if len(unique_companies) >= max_results:
                break

        return await self._fetch_company_filings(
            unique_companies[:max_results], years, max_results
        )

    async def _fetch_company_filings(self, companies: list[str],
                                      years: list[int],
                                      max_results: int = 50) -> list[dict]:
        """Fetch actual 10-K content for specific companies.

        For each company: get submissions → find 10-K filings → fetch HTML →
        extract risk factors. Returns enriched records with filing text.
        """
        results = []

        for company_name in companies:
            if len(results) >= max_results:
                break

            # Find CIK from cached index
            cik = self._find_cik(company_name)
            if not cik:
                continue

            # Fetch submissions to get filing history + document URLs
            submissions = await self._get_submissions(cik)
            if not submissions:
                continue

            actual_name = submissions.get("name", company_name)
            sic = submissions.get("sic", "")
            sic_desc = submissions.get("sicDescription", "")

            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])

            for j, form in enumerate(forms):
                if form != "10-K" or j >= len(dates):
                    continue
                year = int(dates[j][:4]) if dates[j] else 0
                if year not in years:
                    continue
                if len(results) >= max_results:
                    break

                accession = accessions[j] if j < len(accessions) else ""
                primary_doc = primary_docs[j] if j < len(primary_docs) else ""

                if not accession or not primary_doc:
                    continue

                # Fetch actual filing document
                accession_path = accession.replace("-", "")
                doc_url = (f"{SEC_BASE}/Archives/edgar/data/{cik}/"
                           f"{accession_path}/{primary_doc}")

                resp = await self._get(doc_url)
                if not resp:
                    continue

                risk_factors = _extract_risk_factors(resp.text)

                record = {
                    "id": f"{cik}/{accession}",
                    "title": f"{actual_name} — 10-K ({dates[j]})",
                    "type": "sec_filing",
                    "company": actual_name,
                    "cik": cik,
                    "sic": sic,
                    "sic_description": sic_desc,
                    "form_type": "10-K",
                    "date": dates[j],
                    "year": year,
                    "accession": accession,
                    "url": doc_url,
                    "risk_factors_text": (risk_factors or "")[:5000],
                    "risk_factors_length": len(risk_factors) if risk_factors else 0,
                    "risk_factors_word_count": len(risk_factors.split()) if risk_factors else 0,
                }

                # Build an abstract for the LLM
                rf_preview = (risk_factors[:1000] + "...") if risk_factors and len(risk_factors) > 1000 else (risk_factors or "No risk factors extracted")
                record["abstract"] = (
                    f"SEC 10-K Annual Report — {actual_name} (CIK {cik})\n"
                    f"Filed: {dates[j]} | Industry: {sic_desc} (SIC {sic})\n"
                    f"Risk factors: {record['risk_factors_word_count']:,} words\n\n"
                    f"Risk Factors Preview:\n{rf_preview}"
                )

                results.append(record)

        return results

    def _find_cik(self, company_name: str) -> str | None:
        """Find CIK from cached index by company name (fuzzy match)."""
        name_lower = company_name.lower().strip()
        # Try exact match first
        for filing in self._index_cache:
            if filing.get("company", "").lower().strip() == name_lower:
                return filing["cik"]
        # Try substring match
        for filing in self._index_cache:
            if name_lower in filing.get("company", "").lower():
                return filing["cik"]
        return None

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

    Risk Factors is Item 1A, typically between "Item 1A" and "Item 1B".
    The first occurrence is usually in the table of contents — we want
    the SECOND occurrence which is the actual section content.
    """
    # Strip HTML tags for text extraction
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode HTML entities
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text)

    # Find ALL occurrences of "Item 1A" — we want the last substantial one
    pattern = re.compile(r'Item\s+1A[\.\s\-—:]+\s*Risk\s+Factors', re.IGNORECASE)
    matches = list(pattern.finditer(text))

    if not matches:
        # Try broader pattern
        pattern2 = re.compile(r'ITEM\s+1A\b', re.IGNORECASE)
        matches = list(pattern2.finditer(text))

    if not matches:
        return None

    # Use the LAST match — it's the actual section, not the TOC reference.
    # If only one match, use it. If multiple, skip the first (TOC).
    start_match = matches[-1] if len(matches) > 1 else matches[0]
    start_pos = start_match.start()

    # Find the end — "Item 1B" or "Item 2" AFTER the start
    end_patterns = [
        r'Item\s+1B[\.\s\-—:]+',
        r'ITEM\s+1B[\.\s\-—:]+',
        r'Item\s+2[\.\s\-—:]+\s*Properties',
        r'ITEM\s+2[\.\s\-—:]+\s*PROPERTIES',
    ]

    end_pos = len(text)
    for ep in end_patterns:
        match = re.search(ep, text[start_pos + 100:], re.IGNORECASE)
        if match:
            candidate = start_pos + 100 + match.start()
            if candidate < end_pos:
                end_pos = candidate
            break

    risk_text = text[start_pos:end_pos].strip()

    # Skip if too short (probably just a TOC entry)
    if len(risk_text) < 500:
        return None

    # Cap at 100K chars
    if len(risk_text) > 100000:
        risk_text = risk_text[:100000] + "... [truncated]"

    return risk_text
