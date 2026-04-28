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
        super().__init__()
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
                    "risk_factors_text": risk_factors or "",
                    "risk_factors_length": len(risk_factors) if risk_factors else 0,
                    "risk_factors_word_count": len(risk_factors.split()) if risk_factors else 0,
                }

                # Build abstract with comparative context
                rf_preview = (risk_factors[:3000] + "...") if risk_factors and len(risk_factors) > 3000 else (risk_factors or "No risk factors extracted")

                abstract_parts = [
                    f"SEC 10-K Annual Report — {actual_name} (CIK {cik})",
                    f"Filed: {dates[j]} | Industry: {sic_desc} (SIC {sic})",
                    f"Risk factors: {record['risk_factors_word_count']:,} words",
                ]

                # Add previous year context if available from enriched data
                prev_context = self._get_previous_year_context(actual_name, year)
                if prev_context:
                    abstract_parts.append(f"\nPREVIOUS YEAR COMPARISON ({year-1}):")
                    abstract_parts.append(prev_context)

                # Add peer context if available
                peer_context = self._get_peer_context(sic, actual_name, year)
                if peer_context:
                    abstract_parts.append(f"\nPEER GROUP ({sic_desc}):")
                    abstract_parts.append(peer_context)

                abstract_parts.append(f"\nRisk Factors Text:\n{rf_preview}")
                record["abstract"] = "\n".join(abstract_parts)

                results.append(record)

        return results

    def _get_previous_year_context(self, company: str, year: int) -> str | None:
        """Get summary of previous year's risk factors for temporal comparison."""
        if not hasattr(self, '_enriched_filings') or not self._enriched_filings:
            return None

        prev = [f for f in self._enriched_filings
                if f.get("company") == company and f.get("year") == year - 1]
        if not prev:
            return None

        p = prev[0]
        prev_wc = p.get("risk_factors_word_count", 0)
        curr_wc_approx = None  # we don't have current yet at this point in the loop

        lines = [f"  Previous year word count: {prev_wc:,}"]

        # Show first 1000 chars of previous risk factors for comparison
        prev_rf = p.get("risk_factors_text", "")
        if prev_rf:
            lines.append(f"  Previous year preview: {prev_rf[:1000]}...")

        return "\n".join(lines)

    def _get_peer_context(self, sic: str, company: str, year: int) -> str | None:
        """Get peer group summary for cross-company comparison."""
        if not hasattr(self, '_enriched_filings') or not self._enriched_filings:
            return None

        # Find peers: same SIC code, same year, different company
        peers = [f for f in self._enriched_filings
                 if f.get("sic") == sic and f.get("year") == year
                 and f.get("company") != company]

        if not peers:
            return None

        lines = []
        for p in peers[:5]:
            wc = p.get("risk_factors_word_count", 0)
            name = p.get("company", "?")
            lines.append(f"  {name}: {wc:,} words")

        return "\n".join(lines) if lines else None

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
        """Fetch all 10-K filing metadata from quarterly indices.

        Caches enriched filings to catalog/sec_enriched.jsonl. If the cache
        exists and has data, loads from cache instead of re-fetching (~26 min saved).
        """
        import json as _json
        from pathlib import Path as _Path

        # Check for cached enrichment
        cache_path = _Path("catalog/sec_enriched.jsonl")
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            print(f"  [CATALOG] Loading cached enrichment from {cache_path}...")
            enriched = []
            with open(cache_path) as f:
                for line in f:
                    if line.strip():
                        enriched.append(_json.loads(line))
            if enriched:
                companies = len(set(r.get("company", "") for r in enriched))
                print(f"  [CATALOG] Loaded {len(enriched)} filings from {companies} companies (cached)")
                self._enriched_filings = enriched
                # Also populate index cache for fetch() queries
                self._index_cache = enriched
                return enriched

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

        # Phase 2: Enrich ALL operating companies with actual risk factor content
        if progress_callback:
            progress_callback({"phase": "enriching_content", "fetched": len(all_filings),
                               "total_estimated": max_records})

        enriched = await self._enrich_top_companies(all_filings, progress_callback)
        if enriched:
            self._enriched_filings = enriched
            # Cache already saved by _enrich_top_companies (streaming writes)
            return enriched

        return all_filings[:max_records]

    async def _enrich_top_companies(self, all_filings: list[dict],
                                     progress_callback=None) -> list[dict]:
        """Enrich ALL operating companies with actual risk factor content.

        Fetches the actual 10-K document for every filing in the index,
        extracts risk factors. Skips SPV/trust/asset-backed entities
        (identical boilerplate). Caps at 5 filings per company (more adds
        no analytical value). At 10 req/sec, ~5,500 filings takes ~9 min.
        """
        import time as _time

        skip_patterns = ("trust", "receivabl", "mortgage", "asset-backed",
                         "acquisition corp", "certificate", "funding",
                         "auto assets", "auto receivables", "llc series",
                         "depositor", "issuing entity")

        max_filings_per_company = 5

        # Group ALL filings by company, filter SPVs
        company_all_filings = {}
        for f in all_filings:
            name = f.get("company", "")
            if not name or any(p in name.lower() for p in skip_patterns):
                continue
            if name not in company_all_filings:
                company_all_filings[name] = []
            company_all_filings[name].append(f)

        # For each company, select up to max_filings_per_company with YEAR DIVERSITY.
        # Prioritize: one filing per year (most recent first), then fill remaining slots.
        company_filings = {}
        for name, filings in company_all_filings.items():
            # Sort by year descending — most recent first
            filings.sort(key=lambda f: f.get("year", 0), reverse=True)
            # Pick one per year first
            seen_years = set()
            selected = []
            for f in filings:
                yr = f.get("year", 0)
                if yr not in seen_years and len(selected) < max_filings_per_company:
                    selected.append(f)
                    seen_years.add(yr)
            company_filings[name] = selected

        # Flatten to a list of filings to enrich
        filings_to_enrich = []
        for name, filings in company_filings.items():
            filings_to_enrich.extend(filings)

        total = len(filings_to_enrich)
        total_companies = len(company_filings)
        print(f"  [CATALOG] Enriching {total} filings from {total_companies} companies "
              f"(max {max_filings_per_company}/company, SPVs filtered)...")

        import json as _json
        from pathlib import Path as _Path
        cache_path = _Path("catalog/sec_enriched.jsonl")
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        enriched_count = 0
        extraction_attempts = 0
        extraction_successes = 0
        enrich_start = _time.time()
        cache_file = open(cache_path, "w")  # stream writes

        # First, get submissions (SIC codes) for all unique CIKs
        # This is needed for SIC data — batch by unique CIK
        cik_metadata = {}  # cik -> {name, sic, sic_desc}
        unique_ciks = set()
        for f in filings_to_enrich:
            cik = f.get("cik", "")
            if cik:
                unique_ciks.add(cik)

        print(f"  [CATALOG] Fetching metadata for {len(unique_ciks)} unique companies...")
        for i, cik in enumerate(unique_ciks):
            submissions = await self._get_submissions(cik)
            if submissions:
                # Extract primary doc mapping now — don't keep full submissions in memory
                recent = submissions.get("filings", {}).get("recent", {})
                acc_list = recent.get("accessionNumber", [])
                pdoc_list = recent.get("primaryDocument", [])
                pdocs = {}
                for j in range(min(len(acc_list), len(pdoc_list))):
                    if acc_list[j] and pdoc_list[j]:
                        pdocs[acc_list[j]] = pdoc_list[j]
                cik_metadata[cik] = {
                    "name": submissions.get("name", ""),
                    "sic": submissions.get("sic", ""),
                    "sic_description": submissions.get("sicDescription", ""),
                    "_primary_docs": pdocs,
                }
            if (i + 1) % 200 == 0:
                elapsed = _time.time() - enrich_start
                print(f"  [CATALOG] Metadata: {i + 1}/{len(unique_ciks)} companies ({elapsed:.0f}s)")

        # Primary doc mapping already extracted during metadata fetch
        cik_primary_docs = {cik: meta.get("_primary_docs", {})
                            for cik, meta in cik_metadata.items()}

        print(f"  [CATALOG] Fetching filing documents...")

        for i, filing in enumerate(filings_to_enrich):
            cik = filing.get("cik", "")
            accession = filing.get("accession", "")
            company_name = filing.get("company", "")
            date_filed = filing.get("date", "")
            year = filing.get("year", 0)

            if not cik or not accession:
                continue

            # Look up primary document from submissions data
            primary_doc = cik_primary_docs.get(cik, {}).get(accession, "")

            if primary_doc:
                # Direct fetch — no index page needed
                accession_path = accession.replace("-", "")
                doc_url = (f"{SEC_BASE}/Archives/edgar/data/{cik}/"
                           f"{accession_path}/{primary_doc}")
            else:
                # Fallback: fetch the index page to find primary document
                accession_path = accession.replace("-", "")
                index_url = f"{SEC_BASE}/Archives/edgar/data/{cik}/{accession_path}/{accession}-index.htm"
                resp = await self._get(index_url)
                if not resp:
                    extraction_attempts += 1
                    continue

                import re as _re
                doc_links = _re.findall(r'href="([^"]*\.htm[l]?)"', resp.text)
                primary_doc = None
                for link in doc_links:
                    if '-index' in link or 'R1.htm' in link:
                        continue
                    if any(m in link.lower() for m in ('10-k', '10k', 'annual')):
                        primary_doc = link
                        break
                if not primary_doc:
                    for link in doc_links:
                        if '-index' not in link and link.endswith(('.htm', '.html')):
                            primary_doc = link
                            break
                if not primary_doc:
                    extraction_attempts += 1
                    continue

                if primary_doc.startswith('/'):
                    doc_url = f"{SEC_BASE}{primary_doc}"
                else:
                    doc_url = f"{SEC_BASE}/Archives/edgar/data/{cik}/{accession_path}/{primary_doc}"

            resp = await self._get(doc_url)
            extraction_attempts += 1
            if not resp:
                continue

            risk_factors = _extract_risk_factors(resp.text)
            if not risk_factors:
                continue

            extraction_successes += 1
            meta = cik_metadata.get(cik, {})
            record = {
                "id": f"{cik}/{accession}",
                "title": f"{meta.get('name', company_name)} — 10-K ({date_filed})",
                "type": "sec_filing",
                "company": meta.get("name", company_name),
                "cik": cik,
                "sic": meta.get("sic", ""),
                "sic_description": meta.get("sic_description", ""),
                "form_type": "10-K",
                "date": date_filed,
                "year": year,
                "accession": accession,
                "url": doc_url,
                "risk_factors_text": risk_factors,
                "risk_factors_length": len(risk_factors),
                "risk_factors_word_count": len(risk_factors.split()),
            }
            # Stream to disk — don't accumulate in memory
            cache_file.write(_json.dumps(record, default=str) + "\n")
            cache_file.flush()
            enriched_count += 1

            if progress_callback:
                progress_callback({
                    "fetched": enriched_count,
                    "total_estimated": total,
                    "phase": "enriching_content",
                })

            if (i + 1) % 200 == 0:
                elapsed = _time.time() - enrich_start
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (total - i - 1) / rate / 60 if rate > 0 else 0
                success_pct = extraction_successes / max(1, extraction_attempts) * 100
                print(f"  [CATALOG] {i + 1}/{total} filings, "
                      f"{enriched_count} enriched ({success_pct:.0f}% extraction), "
                      f"{elapsed:.0f}s elapsed, ~{eta:.1f}min remaining",
                      flush=True)

        cache_file.close()
        elapsed = _time.time() - enrich_start
        success_pct = extraction_successes / max(1, extraction_attempts) * 100
        print(f"  [CATALOG] Enriched {enriched_count} filings. "
              f"Enrichment time: {elapsed / 60:.1f} minutes. "
              f"Extraction success rate: {success_pct:.0f}%.",
              flush=True)
        print(f"  [CATALOG] Saved to {cache_path}", flush=True)

        # Load from disk for return — the cache IS the data now
        enriched = []
        with open(cache_path) as f:
            for line in f:
                if line.strip():
                    enriched.append(_json.loads(line))
        return enriched

    def filter_schema(self) -> dict:
        return {
            "keyword": {
                "type": "string",
                "description": "Substring matched against company name in the filing index. Supports 'OR' for alternatives, '-prefix' for exclusion.",
                "example": "DUKE ENERGY",
                "required": False,
            },
            "companies": {
                "type": "list[string]",
                "description": "Exact company names to fetch filings for via CIK lookup.",
                "example": ["APPLE INC", "TESLA INC"],
                "required": False,
            },
            "sic": {
                "type": "string",
                "description": "SIC code prefix for industry filtering.",
                "example": "4911",
                "required": False,
            },
            "years": {
                "type": "list[integer]",
                "description": "Filing years to include.",
                "example": [2023, 2024, 2025],
                "required": False,
            },
        }

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

    # Find ALL occurrences of "Item 1A" — pick the one followed by the most text
    # Tolerate whitespace within words caused by HTML tag stripping (e.g. "Ri sk" from "Ri</span>sk")
    pattern = re.compile(r'Item\s+1A[\.\s\-—:]+\s*R\s*i\s*s\s*k\s+F\s*a\s*c\s*t\s*o\s*r\s*s', re.IGNORECASE)
    matches = list(pattern.finditer(text))

    if not matches:
        pattern2 = re.compile(r'ITEM\s+1A\b', re.IGNORECASE)
        matches = list(pattern2.finditer(text))

    if not matches:
        return None

    # Pick the match followed by the MOST text before the next section marker.
    # The actual Item 1A section is always longer than any TOC reference to it.
    end_marker = re.compile(r'Item\s+1B|Item\s+2[\.\s]+Prop', re.IGNORECASE)

    best_match = None
    best_length = 0
    for m in matches:
        # Find the next section marker after this match (no skip — find the nearest one)
        end_search = end_marker.search(text, m.end())
        section_length = (end_search.start() - m.start()) if end_search else (len(text) - m.start())
        if section_length > best_length:
            best_length = section_length
            best_match = m

    if best_match is None:
        best_match = matches[-1]

    start_pos = best_match.start()

    # Find the end — "Item 1B" or "Item 2" AFTER the start
    end_pos = len(text)
    end_search = end_marker.search(text, best_match.end())
    if end_search:
        end_pos = end_search.start()

    risk_text = text[start_pos:end_pos].strip()

    # Skip if too short (probably just a TOC entry)
    if len(risk_text) < 500:
        return None

    # Cap at 100K chars
    # No truncation in extraction — capture the full section.
    # Truncation for LLM context happens at the prompt level.

    return risk_text
