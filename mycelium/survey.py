"""Analytical Survey — multi-technique anomaly detection before AI exploration.

Pure math. Zero LLM cost. Runs 8 independent analytical techniques across
ALL records. Records flagged by MULTIPLE techniques are the highest-priority
investigation targets.

Techniques:
  1. Basic statistics (distributions, z-score outliers, concentrations)
  2. Isolation Forest (multi-dimensional outlier detection)
  3. TF-IDF text analysis (unusual text content)
  4. DBSCAN clustering (records that don't belong to any cluster)
  5. Entity concentration (entities with outsized influence)
  6. Graph analysis (centrality, inversions, orphans)
  7. Temporal analysis (stale-but-active, velocity anomalies)
  8. Keyword signals (uncommon keywords correlating with extreme values)

All analysis is domain-agnostic. Works on npm packages, SEC filings,
hospital records, or any other list of dicts.
"""

import re
import numpy as np
import pandas as pd
from collections import Counter, defaultdict
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import DBSCAN


class AnalyticalSurvey:
    """Runs multiple analytical techniques across ALL records."""

    def analyze(self, records: list[dict], progress_callback=None) -> dict:
        """Analyze records with 8 independent techniques.

        Returns dict with per-technique anomalies and multi-flagged records.
        """
        if not records:
            return {"error": "No records provided", "record_count": 0}

        df = pd.DataFrame(records)

        results = {
            "record_count": len(df),
            "fields": list(df.columns),
            "techniques_applied": [],
            "anomalies_by_technique": {},
            "multi_flagged": [],
            # Backwards-compatible keys for orchestrator
            "anomaly_clusters": [],
            "outliers": [],
            "concentrations": [],
            "unusual_combinations": [],
            "distributions": {},
            "correlations": [],
            "field_types": {},
        }

        anomaly_flags = defaultdict(set)

        # Detect field types
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        text_cols = df.select_dtypes(include=["object"]).columns.tolist()
        cat_cols = [c for c in text_cols if df[c].nunique() < len(df) * 0.3 and df[c].nunique() > 1]

        # Find best text column (longest average content)
        text_col = None
        best_avg = 0
        for col in text_cols:
            avg_len = df[col].fillna("").str.len().mean()
            if avg_len > best_avg:
                best_avg = avg_len
                text_col = col

        # Detect date columns — require string values containing date separators
        date_cols = []
        for col in df.columns:
            if col in numeric_cols:
                continue
            try:
                sample = df[col].dropna().head(20)
                if len(sample) == 0:
                    continue
                # Must be strings containing date-like patterns (-, /, T)
                str_sample = sample.astype(str)
                has_date_chars = str_sample.str.contains(r'\d{4}[-/]\d{2}', regex=True).mean()
                if has_date_chars > 0.5:
                    pd.to_datetime(sample)
                    date_cols.append(col)
            except Exception:
                pass

        # Build field_types for backwards compat
        for col in numeric_cols:
            results["field_types"][col] = "numeric"
        for col in cat_cols:
            results["field_types"][col] = "categorical"
        for col in date_cols:
            results["field_types"][col] = "temporal"

        def _progress(phase):
            if progress_callback:
                progress_callback({"phase": phase, "complete": True})

        # ── TECHNIQUE 1: Basic Statistics ──
        stats = self._basic_statistics(df, numeric_cols, cat_cols)
        results["distributions"] = stats.get("distributions", {})
        results["outliers"] = stats.get("outliers", [])
        results["concentrations"] = stats.get("concentrations", [])
        results["correlations"] = stats.get("correlations", [])
        results["unusual_combinations"] = stats.get("unusual_combinations", [])

        for idx in stats.get("outlier_indices", []):
            anomaly_flags[idx].add("basic_statistics")

        results["anomalies_by_technique"]["basic_statistics"] = {
            "count": len(stats.get("outlier_indices", [])),
            "description": f"{len(stats.get('outlier_indices', []))} z-score outliers across {len(numeric_cols)} numeric fields",
        }
        results["techniques_applied"].append("basic_statistics")
        _progress("basic_statistics")

        # ── TECHNIQUE 2: Isolation Forest ──
        if len(numeric_cols) >= 2:
            iso_indices = self._isolation_forest(df, numeric_cols)
            for idx in iso_indices:
                anomaly_flags[idx].add("isolation_forest")
            results["anomalies_by_technique"]["isolation_forest"] = {
                "count": len(iso_indices),
                "description": (
                    f"{len(iso_indices)} multi-dimensional outliers "
                    f"across {len(numeric_cols)} numeric fields"
                ),
                "examples": [self._record_summary(df, idx) for idx in iso_indices[:10]],
            }
            results["techniques_applied"].append("isolation_forest")
        _progress("isolation_forest")

        # ── TECHNIQUE 3: TF-IDF Text Analysis ──
        if text_col:
            tfidf_indices = self._tfidf_analysis(df, text_col)
            for idx in tfidf_indices:
                anomaly_flags[idx].add("tfidf")
            results["anomalies_by_technique"]["tfidf"] = {
                "count": len(tfidf_indices),
                "description": f"{len(tfidf_indices)} records have unusual text in '{text_col}'",
                "examples": [self._record_summary(df, idx) for idx in tfidf_indices[:10]],
            }
            results["techniques_applied"].append("tfidf_text_analysis")
        _progress("tfidf")

        # ── TECHNIQUE 4: DBSCAN Clustering ──
        if len(numeric_cols) >= 2:
            cluster_indices = self._dbscan_outliers(df, numeric_cols)
            for idx in cluster_indices:
                anomaly_flags[idx].add("clustering")
            results["anomalies_by_technique"]["clustering"] = {
                "count": len(cluster_indices),
                "description": f"{len(cluster_indices)} records don't belong to any natural cluster",
                "examples": [self._record_summary(df, idx) for idx in cluster_indices[:10]],
            }
            results["techniques_applied"].append("dbscan_clustering")
        _progress("clustering")

        # ── TECHNIQUE 5: Entity Concentration ──
        entity_results = self._entity_concentration(df, cat_cols, numeric_cols)
        entity_flagged = set()
        for info in entity_results.values():
            for ent in info.get("entities", []):
                # Flag all records belonging to concentrated entities
                entity_name = ent.get("entity", "")
                for col in cat_cols:
                    mask = df[col] == entity_name
                    entity_flagged.update(df[mask].index.tolist())
        for idx in entity_flagged:
            anomaly_flags[idx].add("entity_concentration")
        results["anomalies_by_technique"]["entity_concentration"] = entity_results
        results["techniques_applied"].append("entity_concentration")
        _progress("entity_concentration")

        # ── TECHNIQUE 6: Graph Analysis ──
        graph = self._build_graph(df)
        if graph:
            graph_results = self._graph_analysis(df, graph)
            for idx in graph_results.get("anomaly_indices", []):
                anomaly_flags[idx].add("graph")
            results["anomalies_by_technique"]["graph"] = graph_results
            results["techniques_applied"].append("graph_analysis")
        _progress("graph")

        # ── TECHNIQUE 7: Temporal Analysis ──
        if date_cols:
            temporal_indices = self._temporal_analysis(df, date_cols[0], numeric_cols)
            for idx in temporal_indices:
                anomaly_flags[idx].add("temporal")
            results["anomalies_by_technique"]["temporal"] = {
                "count": len(temporal_indices),
                "description": f"{len(temporal_indices)} temporal anomalies (stale-but-active or high-velocity)",
                "examples": [self._record_summary(df, idx) for idx in temporal_indices[:10]],
            }
            results["techniques_applied"].append("temporal_analysis")
        _progress("temporal")

        # ── TECHNIQUE 8: Keyword Signals ──
        if text_col:
            kw_results = self._keyword_signal_analysis(df, text_col, numeric_cols)
            for idx in kw_results.get("flagged_indices", []):
                anomaly_flags[idx].add("keywords")
            results["anomalies_by_technique"]["keywords"] = kw_results
            results["techniques_applied"].append("keyword_signals")

        # Also check other text columns for keywords
        for col in text_cols:
            if col == text_col or col in date_cols:
                continue
            avg_len = df[col].fillna("").str.len().mean()
            if avg_len > 5:
                kw2 = self._keyword_signal_analysis(df, col, numeric_cols)
                for idx in kw2.get("flagged_indices", []):
                    anomaly_flags[idx].add("keywords")
        _progress("keywords")

        # ── TECHNIQUE 9: Temporal Text Comparison ──
        # Compare consecutive documents from the same entity over time.
        # Flags: major rewrites, term removals, word count shifts.
        group_col = self._find_group_col(df, cat_cols)
        if text_col and group_col and date_cols:
            temporal_text = self._temporal_text_comparison(df, text_col, group_col, date_cols[0])
            for item in temporal_text.get("anomalies", []):
                for idx in item.get("indices", []):
                    anomaly_flags[idx].add("temporal_text")
            results["anomalies_by_technique"]["temporal_text"] = temporal_text
            results["techniques_applied"].append("temporal_text_comparison")
        _progress("temporal_text")

        # ── TECHNIQUE 10: Peer Divergence ──
        # Within a peer group (same category), find records whose text diverges.
        peer_col = self._find_peer_col(df, cat_cols)
        if text_col and group_col and peer_col:
            peer_div = self._peer_divergence(df, text_col, group_col, peer_col)
            for item in peer_div.get("anomalies", []):
                for idx in item.get("indices", []):
                    anomaly_flags[idx].add("peer_divergence")
            results["anomalies_by_technique"]["peer_divergence"] = peer_div
            results["techniques_applied"].append("peer_divergence")
        _progress("peer_divergence")

        # ── CONVERGENCE: Multi-flagged records ──
        multi_flagged = []
        for idx, techniques in anomaly_flags.items():
            if len(techniques) >= 2:
                multi_flagged.append({
                    "record": self._record_summary(df, idx),
                    "flagged_by": sorted(techniques),
                    "technique_count": len(techniques),
                    "description": (
                        f"Flagged by {len(techniques)} techniques: "
                        f"{', '.join(sorted(techniques))}"
                    ),
                })

        multi_flagged.sort(key=lambda x: x["technique_count"], reverse=True)
        results["multi_flagged"] = multi_flagged
        results["single_flagged_count"] = sum(1 for t in anomaly_flags.values() if len(t) == 1)
        results["multi_flagged_count"] = len(multi_flagged)
        results["unflagged_count"] = len(df) - len(anomaly_flags)

        results["summary"] = (
            f"Analyzed {len(df)} records with {len(results['techniques_applied'])} techniques. "
            f"{results['unflagged_count']} normal, "
            f"{results['single_flagged_count']} flagged by 1 technique, "
            f"{results['multi_flagged_count']} flagged by 2+ techniques (high priority)."
        )

        # Build anomaly_clusters from multi-flagged for backwards compat
        results["anomaly_clusters"] = self._build_clusters(results)

        return results

    # =================================================================
    # TECHNIQUE 1: Basic Statistics
    # =================================================================

    def _basic_statistics(self, df: pd.DataFrame, numeric_cols: list,
                          cat_cols: list) -> dict:
        """Distributions, z-score outliers, concentrations, correlations."""
        result = {
            "distributions": {},
            "outliers": [],
            "outlier_indices": [],
            "concentrations": [],
            "correlations": [],
            "unusual_combinations": [],
        }

        # Numeric distributions
        for col in numeric_cols:
            vals = df[col].dropna()
            if len(vals) < 10:
                continue
            result["distributions"][col] = {
                "type": "numeric",
                "count": len(vals),
                "min": float(vals.min()),
                "max": float(vals.max()),
                "mean": round(float(vals.mean()), 2),
                "median": float(vals.median()),
                "std": round(float(vals.std()), 2),
                "zeros": int((vals == 0).sum()),
            }

        # Categorical distributions
        for col in cat_cols:
            vals = df[col].dropna()
            if len(vals) < 10:
                continue
            counter = vals.value_counts()
            total = len(vals)
            result["distributions"][col] = {
                "type": "categorical",
                "count": total,
                "unique_values": int(counter.nunique()) if hasattr(counter, 'nunique') else len(counter),
                "top_10": [
                    {"value": str(v), "count": int(c), "pct": round(c / total * 100, 1)}
                    for v, c in counter.head(10).items()
                ],
            }

        # Z-score outliers
        for col in numeric_cols:
            vals = df[col].dropna()
            if len(vals) < 20:
                continue
            mean, std = vals.mean(), vals.std()
            if std == 0:
                continue

            for idx in df.index:
                val = df.at[idx, col]
                if pd.isna(val):
                    continue
                z = (val - mean) / std
                if abs(z) > 2:
                    result["outlier_indices"].append(idx)
                    result["outliers"].append({
                        "field": col,
                        "value": float(val),
                        "z_score": round(float(z), 2),
                        "direction": "high" if z > 0 else "low",
                        "record_id": self._get_id(df, idx),
                        "record_summary": str(self._record_summary(df, idx)),
                    })

        result["outlier_indices"] = list(set(result["outlier_indices"]))
        # Keep top 50 outliers by z-score
        result["outliers"].sort(key=lambda x: abs(x["z_score"]), reverse=True)
        result["outliers"] = result["outliers"][:50]

        # Concentrations
        for col in cat_cols:
            vals = df[col].dropna()
            if len(vals) < 20:
                continue
            counter = vals.value_counts()
            total = len(vals)
            top3_pct = counter.head(3).sum() / total * 100
            if top3_pct > 50:
                result["concentrations"].append({
                    "field": col,
                    "concentration_pct": round(top3_pct, 1),
                    "top_values": [
                        {"value": str(v), "count": int(c), "pct": round(c / total * 100, 1)}
                        for v, c in counter.head(3).items()
                    ],
                    "severity": "high" if top3_pct > 80 else "medium" if top3_pct > 60 else "moderate",
                })

        # Correlations
        if len(numeric_cols) >= 2:
            corr_matrix = df[numeric_cols].corr()
            for i in range(len(numeric_cols)):
                for j in range(i + 1, len(numeric_cols)):
                    c = corr_matrix.iloc[i, j]
                    if abs(c) > 0.5 and not pd.isna(c):
                        result["correlations"].append({
                            "field_a": numeric_cols[i],
                            "field_b": numeric_cols[j],
                            "correlation": round(float(c), 3),
                            "strength": "strong" if abs(c) > 0.8 else "moderate",
                        })

        return result

    # =================================================================
    # TECHNIQUE 2: Isolation Forest
    # =================================================================

    def _isolation_forest(self, df: pd.DataFrame, numeric_cols: list) -> list[int]:
        """Multi-dimensional outlier detection."""
        data = df[numeric_cols].fillna(0).values
        scaler = StandardScaler()
        scaled = scaler.fit_transform(data)

        model = IsolationForest(contamination=0.05, random_state=42)
        predictions = model.fit_predict(scaled)

        return [i for i, p in enumerate(predictions) if p == -1]

    # =================================================================
    # TECHNIQUE 3: TF-IDF Text Analysis
    # =================================================================

    def _tfidf_analysis(self, df: pd.DataFrame, text_col: str) -> list[int]:
        """Find records with unusual text content."""
        texts = df[text_col].fillna("").tolist()

        try:
            # token_pattern excludes pure numbers and very short tokens
            vectorizer = TfidfVectorizer(max_features=1000, stop_words="english",
                                         token_pattern=r'(?u)\b[a-zA-Z][a-zA-Z]{2,}\b')
            tfidf_matrix = vectorizer.fit_transform(texts)
        except Exception:
            return []

        # Distance from centroid
        centroid = np.asarray(tfidf_matrix.mean(axis=0)).flatten()
        distances = []
        for i in range(tfidf_matrix.shape[0]):
            row = np.asarray(tfidf_matrix[i].todense()).flatten()
            dist = float(np.sqrt(((row - centroid) ** 2).sum()))
            distances.append(dist)

        distances = np.array(distances)
        threshold = np.percentile(distances, 95)

        return [int(i) for i, d in enumerate(distances) if d > threshold]

    # =================================================================
    # TECHNIQUE 4: DBSCAN Clustering
    # =================================================================

    def _dbscan_outliers(self, df: pd.DataFrame, numeric_cols: list) -> list[int]:
        """Find records that don't belong to any cluster."""
        data = df[numeric_cols].fillna(0).values
        scaler = StandardScaler()
        scaled = scaler.fit_transform(data)

        model = DBSCAN(eps=1.5, min_samples=5)
        labels = model.fit_predict(scaled)

        return [int(i) for i, l in enumerate(labels) if l == -1]

    # =================================================================
    # TECHNIQUE 5: Entity Concentration
    # =================================================================

    def _entity_concentration(self, df: pd.DataFrame, cat_cols: list,
                               numeric_cols: list) -> dict:
        """Find entities with outsized influence."""
        results = {}

        for cat_col in cat_cols[:8]:
            for num_col in numeric_cols[:5]:
                try:
                    grouped = df.groupby(cat_col)[num_col].agg(["sum", "count", "mean"])
                except Exception:
                    continue

                total = grouped["sum"].sum()
                if total == 0:
                    continue

                concentrated = grouped[grouped["sum"] > total * 0.05].sort_values(
                    "sum", ascending=False
                )

                if 0 < len(concentrated) < len(grouped) * 0.1:
                    key = f"{cat_col}_x_{num_col}"
                    results[key] = {
                        "description": (
                            f"{len(concentrated)} '{cat_col}' values control "
                            f">5% of total '{num_col}'"
                        ),
                        "entities": [
                            {
                                "entity": str(idx),
                                "total": float(row["sum"]),
                                "count": int(row["count"]),
                                "pct_of_total": round(float(row["sum"] / total * 100), 1),
                            }
                            for idx, row in concentrated.head(10).iterrows()
                        ],
                    }

        return results

    # =================================================================
    # TECHNIQUE 6: Graph Analysis
    # =================================================================

    def _build_graph(self, df: pd.DataFrame) -> dict | None:
        """Build directed graph from list/dict fields."""
        id_col = None
        for col in ("name", "id", "key", "title"):
            if col in df.columns:
                id_col = col
                break
        if not id_col:
            return None

        # Find list/dict fields
        list_cols = []
        for col in df.columns:
            sample = df[col].dropna().head(50)
            if sample.apply(lambda x: isinstance(x, (list, dict))).any():
                list_cols.append(col)

        if not list_cols:
            return None

        edges = []
        nodes = set()

        for _, row in df.iterrows():
            source = str(row.get(id_col, ""))
            if not source:
                continue
            nodes.add(source)

            for lc in list_cols:
                deps = row.get(lc)
                if isinstance(deps, dict):
                    deps = list(deps.keys())
                elif isinstance(deps, str):
                    deps = [d.strip() for d in deps.split(",") if d.strip()]
                elif not isinstance(deps, list):
                    continue

                for dep in deps:
                    dep = str(dep).strip()
                    if dep and dep != source:
                        edges.append((source, dep))
                        nodes.add(dep)

        if not edges:
            return None

        return {"nodes": nodes, "edges": edges, "id_col": id_col}

    def _graph_analysis(self, df: pd.DataFrame, graph: dict) -> dict:
        """Centrality, inversions, orphans."""
        results = {"anomaly_indices": [], "findings": []}

        in_degree = Counter()
        for source, target in graph["edges"]:
            in_degree[target] += 1

        if not in_degree:
            return results

        mean_in = np.mean(list(in_degree.values()))
        id_col = graph["id_col"]
        record_ids = set(df[id_col].dropna().astype(str))

        # High in-degree
        for node, degree in in_degree.most_common(15):
            if degree > mean_in * 5 and degree >= 5:
                results["findings"].append({
                    "type": "high_in_degree",
                    "node": node,
                    "in_degree": degree,
                    "description": (
                        f"'{node}' is depended on by {degree} records "
                        f"({degree / max(mean_in, 1):.0f}x average)"
                    ),
                })
                # Flag records that are this node
                mask = df[id_col].astype(str) == node
                results["anomaly_indices"].extend(df[mask].index.tolist())

        # Dependency inversions
        lookup = {str(row[id_col]): row for _, row in df.iterrows() if pd.notna(row.get(id_col))}
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

        for source, target in graph["edges"]:
            if source not in lookup or target not in lookup:
                continue
            for num_col in numeric_cols[:5]:
                sv = lookup[source].get(num_col)
                tv = lookup[target].get(num_col)
                if pd.notna(sv) and pd.notna(tv) and sv > 0 and tv > sv * 1.5:
                    results["findings"].append({
                        "type": "dependency_inversion",
                        "source": source,
                        "dependency": target,
                        "field": num_col,
                        "ratio": round(float(tv / sv), 2),
                        "description": (
                            f"'{target}' (dep) has {tv:,.0f} '{num_col}' while "
                            f"'{source}' (parent) has {sv:,.0f} — {tv / sv:.1f}x"
                        ),
                    })

        # External dependencies
        external = {n: in_degree[n] for n in in_degree if n not in record_ids and in_degree[n] >= 3}
        if external:
            top_ext = sorted(external.items(), key=lambda x: x[1], reverse=True)[:10]
            results["findings"].append({
                "type": "external_dependencies",
                "count": len(external),
                "top": [{"name": n, "depended_by": c} for n, c in top_ext],
                "description": f"{len(external)} external dependencies not in the catalog",
            })

        return results

    # =================================================================
    # TECHNIQUE 7: Temporal Analysis
    # =================================================================

    def _temporal_analysis(self, df: pd.DataFrame, date_col: str,
                            numeric_cols: list) -> list[int]:
        """Stale-but-active and velocity anomalies."""
        anomaly_indices = []

        try:
            dates = pd.to_datetime(df[date_col], errors="coerce", utc=True)
        except Exception:
            return anomaly_indices

        now = pd.Timestamp.now(tz="UTC")
        age_days = (now - dates).dt.days

        for num_col in numeric_cols:
            vals = df[num_col].fillna(0)

            # Stale but high value (>2 years old, above 75th percentile)
            q75 = vals.quantile(0.75)
            if q75 > 0:
                stale_mask = (age_days > 730) & (vals > q75)
                anomaly_indices.extend(df[stale_mask].index.tolist())

            # High velocity (value / age)
            safe_age = age_days.clip(lower=1) / 365.25
            velocity = vals / safe_age
            vel_threshold = velocity.quantile(0.95)
            if vel_threshold > 0:
                fast_mask = velocity > vel_threshold
                anomaly_indices.extend(df[fast_mask].index.tolist())

        return list(set(anomaly_indices))

    # =================================================================
    # TECHNIQUE 8: Keyword Signals
    # =================================================================

    def _keyword_signal_analysis(self, df: pd.DataFrame, text_col: str,
                                  numeric_cols: list) -> dict:
        """Uncommon keywords correlating with extreme numeric values."""
        all_words = Counter()
        record_words = {}

        for idx, text in df[text_col].fillna("").items():
            words = set(re.findall(r"[a-z]{3,}", str(text).lower()))
            record_words[idx] = words
            all_words.update(words)

        total = len(df)
        signal_words = {w: c for w, c in all_words.items() if 3 <= c <= total * 0.05}

        flagged = []
        anomalies = []

        for word, count in list(signal_words.items())[:50]:
            word_mask = pd.Series(False, index=df.index)
            for idx, words in record_words.items():
                if word in words:
                    word_mask[idx] = True

            if word_mask.sum() < 3:
                continue

            for num_col in numeric_cols:
                word_mean = df.loc[word_mask, num_col].mean()
                overall_mean = df[num_col].mean()

                if overall_mean > 0 and word_mean > 0:
                    ratio = word_mean / overall_mean
                    if ratio > 3.0 or (ratio < 0.33 and word_mean > 0):
                        anomalies.append({
                            "keyword": word,
                            "text_field": text_col,
                            "numeric_field": num_col,
                            "ratio": round(float(ratio), 2),
                            "keyword_count": count,
                            "description": (
                                f"'{word}' records have {ratio:.1f}x average '{num_col}' "
                                f"({word_mean:.0f} vs {overall_mean:.0f})"
                            ),
                            "examples": [
                                self._get_id(df, idx)
                                for idx in df[word_mask].index[:5]
                            ],
                        })
                        flagged.extend(df[word_mask].index.tolist())

        return {
            "anomalies": sorted(anomalies, key=lambda x: abs(x.get("ratio", 1) - 1), reverse=True)[:20],
            "flagged_indices": list(set(flagged)),
        }

    # =================================================================
    # Cluster builder (backwards compat)
    # =================================================================

    # =================================================================
    # TECHNIQUE 9: Temporal Text Comparison
    # =================================================================

    def _find_group_col(self, df: pd.DataFrame, cat_cols: list) -> str | None:
        """Find the best column to group records by entity (company, author, etc.)."""
        # Prefer columns named company, author, publisher, maintainer
        for name in ("company", "author", "publisher", "maintainer", "name"):
            if name in df.columns:
                n_unique = df[name].nunique()
                if 2 <= n_unique <= len(df) * 0.5:
                    return name
        # Fall back to any cat col with reasonable cardinality
        for col in cat_cols:
            n_unique = df[col].nunique()
            if 3 <= n_unique <= len(df) * 0.3:
                return col
        return None

    def _find_peer_col(self, df: pd.DataFrame, cat_cols: list) -> str | None:
        """Find the best column for peer grouping (industry, category, etc.)."""
        for name in ("sic", "sic_description", "category", "industry", "sector", "search_term"):
            if name in df.columns:
                n_unique = df[name].nunique()
                if 2 <= n_unique <= 50:
                    return name
        return None

    def _temporal_text_comparison(self, df: pd.DataFrame, text_col: str,
                                   group_col: str, date_col: str) -> dict:
        """Compare consecutive documents from the same entity over time.

        Flags: major rewrites (low cosine similarity), term removals,
        word count shifts >30%.
        """
        from sklearn.metrics.pairwise import cosine_similarity

        results = {"anomalies": [], "description": ""}

        # Build TF-IDF across all documents
        texts = df[text_col].fillna("").tolist()
        try:
            vectorizer = TfidfVectorizer(max_features=5000, stop_words="english",
                                         ngram_range=(1, 2),
                                         token_pattern=r'(?u)\b[a-zA-Z][a-zA-Z]{2,}\b')
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
        except Exception:
            return results

        # Group by entity, sort by date
        groups = df.groupby(group_col)

        for entity_name, group in groups:
            if len(group) < 2:
                continue

            sorted_group = group.sort_values(date_col)
            indices = sorted_group.index.tolist()

            for k in range(1, len(indices)):
                prev_idx = indices[k - 1]
                curr_idx = indices[k]

                prev_row = df.loc[prev_idx]
                curr_row = df.loc[curr_idx]

                # Cosine similarity between consecutive years
                prev_vec = tfidf_matrix[df.index.get_loc(prev_idx)]
                curr_vec = tfidf_matrix[df.index.get_loc(curr_idx)]
                sim = float(cosine_similarity(prev_vec, curr_vec)[0][0])

                prev_date = str(prev_row.get(date_col, "?"))[:10]
                curr_date = str(curr_row.get(date_col, "?"))[:10]

                # Major rewrite — extract the terms that DIFFER most
                if sim < 0.7:
                    prev_arr = np.asarray(prev_vec.todense()).flatten()
                    curr_arr = np.asarray(curr_vec.todense()).flatten()
                    diff = prev_arr - curr_arr
                    sorted_indices = np.argsort(diff)

                    # Terms stronger in previous (removed/weakened)
                    removed = [str(feature_names[i]) for i in sorted_indices[-10:]
                               if diff[i] > 0.01]
                    # Terms stronger in current (added/strengthened)
                    added = [str(feature_names[i]) for i in sorted_indices[:10]
                             if diff[i] < -0.01]

                    results["anomalies"].append({
                        "type": "major_rewrite",
                        "entity": str(entity_name),
                        "from_date": prev_date,
                        "to_date": curr_date,
                        "cosine_similarity": round(sim, 3),
                        "indices": [prev_idx, curr_idx],
                        "description": (
                            f"{entity_name}: major text rewrite between {prev_date} "
                            f"and {curr_date} (cosine similarity {sim:.2f})"
                        ),
                        "evidence": {
                            "terms_removed_or_weakened": removed,
                            "terms_added_or_strengthened": added,
                            "prev_word_count": len(str(prev_row.get(text_col, "")).split()),
                            "curr_word_count": len(str(curr_row.get(text_col, "")).split()),
                        },
                    })

                # Word count shift
                prev_wc = len(str(prev_row.get(text_col, "")).split())
                curr_wc = len(str(curr_row.get(text_col, "")).split())
                if prev_wc > 100:
                    change_pct = (curr_wc - prev_wc) / prev_wc * 100
                    if abs(change_pct) > 30:
                        results["anomalies"].append({
                            "type": "word_count_shift",
                            "entity": str(entity_name),
                            "from_date": prev_date,
                            "to_date": curr_date,
                            "prev_words": prev_wc,
                            "curr_words": curr_wc,
                            "change_pct": round(change_pct, 1),
                            "indices": [curr_idx],
                            "description": (
                                f"{entity_name}: word count {'grew' if change_pct > 0 else 'shrank'} "
                                f"{abs(change_pct):.0f}% ({prev_wc:,} → {curr_wc:,}) "
                                f"between {prev_date} and {curr_date}"
                            ),
                            "evidence": {
                                "previous": {"date": prev_date, "word_count": prev_wc},
                                "current": {"date": curr_date, "word_count": curr_wc},
                                "change": curr_wc - prev_wc,
                                "change_pct": round(change_pct, 1),
                            },
                        })

                # Terms that disappeared
                prev_loc = df.index.get_loc(prev_idx)
                curr_loc = df.index.get_loc(curr_idx)
                prev_terms = set(feature_names[np.asarray(tfidf_matrix[prev_loc].todense()).flatten() > 0])
                curr_terms = set(feature_names[np.asarray(tfidf_matrix[curr_loc].todense()).flatten() > 0])

                disappeared = prev_terms - curr_terms
                appeared = curr_terms - prev_terms

                # Filter to significant terms (not just rare noise)
                sig_disappeared = [t for t in disappeared if len(t) > 4][:10]
                sig_appeared = [t for t in appeared if len(t) > 4][:10]

                if len(sig_disappeared) > 3:
                    results["anomalies"].append({
                        "type": "terms_removed",
                        "entity": str(entity_name),
                        "from_date": prev_date,
                        "to_date": curr_date,
                        "removed_terms": sig_disappeared,
                        "indices": [curr_idx],
                        "description": (
                            f"{entity_name}: removed {len(sig_disappeared)} terms between "
                            f"{prev_date} and {curr_date}: {', '.join(sig_disappeared[:5])}"
                        ),
                    })

        results["anomalies"].sort(key=lambda x: x.get("cosine_similarity", 1))
        results["description"] = f"{len(results['anomalies'])} temporal text anomalies found"
        return results

    # =================================================================
    # TECHNIQUE 10: Peer Divergence
    # =================================================================

    def _peer_divergence(self, df: pd.DataFrame, text_col: str,
                          group_col: str, peer_col: str) -> dict:
        """Find entities whose text diverges from peers in the same category.

        If 4/5 semiconductor companies mention "export control" but one doesn't,
        flag the outlier.
        """
        results = {"anomalies": [], "description": ""}

        texts = df[text_col].fillna("").tolist()
        try:
            vectorizer = TfidfVectorizer(max_features=3000, stop_words="english",
                                         ngram_range=(1, 2),
                                         token_pattern=r'(?u)\b[a-zA-Z][a-zA-Z]{2,}\b')
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
        except Exception:
            return results

        # Get the latest record per entity (most recent filing)
        latest = df.sort_values("year" if "year" in df.columns else group_col, ascending=False)
        latest = latest.drop_duplicates(subset=[group_col], keep="first")

        # Group by peer category
        peer_groups = latest.groupby(peer_col)

        for peer_name, peer_group in peer_groups:
            if len(peer_group) < 3:
                continue

            peer_indices = [df.index.get_loc(idx) for idx in peer_group.index]

            # For each significant term, check if any peer is missing it
            for term_idx, term in enumerate(feature_names):
                if len(term) < 5:
                    continue

                usage = [tfidf_matrix[pi, term_idx] > 0 for pi in peer_indices]
                usage_pct = sum(usage) / len(usage)

                # Term used by 80%+ of peers — flag the outliers
                if usage_pct >= 0.8 and sum(usage) < len(usage):
                    outliers = [
                        str(peer_group.iloc[i][group_col])
                        for i, used in enumerate(usage) if not used
                    ]
                    if outliers:
                        peers_using = [
                            str(peer_group.iloc[i][group_col])
                            for i, used in enumerate(usage) if used
                        ]
                        results["anomalies"].append({
                            "type": "peer_term_absence",
                            "term": term,
                            "peer_group": str(peer_name),
                            "usage_pct": round(usage_pct * 100, 0),
                            "outliers": outliers,
                            "indices": [peer_group.index[i] for i, used in enumerate(usage) if not used],
                            "description": (
                                f"Term '{term}' used by {usage_pct * 100:.0f}% of {peer_name} peers "
                                f"but ABSENT from: {', '.join(outliers)}"
                            ),
                            "evidence": {
                                "term": term,
                                "peers_using_term": peers_using[:10],
                                "peers_missing_term": outliers,
                                "peer_group_size": len(peer_group),
                            },
                        })

                # Term unique to one entity — no peers use it
                if 0 < sum(usage) <= 1 and len(usage) >= 4:
                    unique_users = [
                        str(peer_group.iloc[i][group_col])
                        for i, used in enumerate(usage) if used
                    ]
                    non_users = [
                        str(peer_group.iloc[i][group_col])
                        for i, used in enumerate(usage) if not used
                    ]
                    results["anomalies"].append({
                        "type": "unique_risk_term",
                        "term": term,
                        "peer_group": str(peer_name),
                        "unique_to": unique_users,
                        "indices": [peer_group.index[i] for i, used in enumerate(usage) if used],
                        "description": (
                            f"Only {', '.join(unique_users)} mentions '{term}' — "
                            f"no other {peer_name} peer uses this term"
                        ),
                        "evidence": {
                            "term": term,
                            "used_by": unique_users,
                            "not_used_by": non_users[:10],
                            "peer_group_size": len(peer_group),
                        },
                    })

        # Deduplicate and limit
        seen = set()
        unique = []
        for a in results["anomalies"]:
            key = (a["type"], a.get("term", ""), tuple(a.get("outliers", a.get("unique_to", []))))
            if key not in seen:
                seen.add(key)
                unique.append(a)
        results["anomalies"] = unique[:50]
        results["description"] = f"{len(results['anomalies'])} peer divergence anomalies found"
        return results

    # =================================================================
    # Cluster builder (backwards compat)
    # =================================================================

    def _build_clusters(self, results: dict) -> list[dict]:
        """Build anomaly_clusters from multi-flagged records and technique results."""
        clusters = []

        # High-priority: multi-flagged records
        for mf in results.get("multi_flagged", [])[:20]:
            clusters.append({
                "name": mf["description"][:100],
                "severity": "high" if mf["technique_count"] >= 3 else "medium",
                "evidence_count": mf["technique_count"],
                "description": mf["description"],
                "detail": mf,
            })

        # Per-technique highlights
        for tech_name, tech_data in results.get("anomalies_by_technique", {}).items():
            if isinstance(tech_data, dict):
                desc = tech_data.get("description", tech_name)
                count = tech_data.get("count", 0)
                if count > 0:
                    clusters.append({
                        "name": desc[:100],
                        "severity": "moderate",
                        "evidence_count": count,
                        "description": desc,
                    })

        return clusters

    # =================================================================
    # Helpers
    # =================================================================

    def _get_id(self, df: pd.DataFrame, idx) -> str:
        """Get the best identifier for a record."""
        row = df.iloc[idx] if isinstance(idx, int) else df.loc[idx]
        for col in ("name", "id", "title", "key"):
            if col in df.columns and pd.notna(row.get(col)):
                return str(row[col])
        return f"row_{idx}"

    def _record_summary(self, df: pd.DataFrame, idx) -> dict:
        """Summarize a record for display."""
        row = df.iloc[idx] if isinstance(idx, int) else df.loc[idx]
        summary = {}
        for col in df.columns[:10]:
            val = row.get(col) if isinstance(row, dict) else row[col]
            if pd.notna(val) and val != "" and val != 0:
                if isinstance(val, (list, dict)):
                    continue
                summary[col] = val
        return summary


# Backwards compat alias
ProgrammaticSurvey = AnalyticalSurvey
