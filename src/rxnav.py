"""Small client for the NLM RxNav REST API with caching, rate limiting and retries.

Every successful response is cached in a SQLite file under data/cache/ keyed by
the full request URL, so reruns never re-query. Endpoints used (verified
against https://lhncbc.nlm.nih.gov/RxNav/APIs/ in Oct 2026):

    /ndcstatus.json?ndc=            NDC -> RxCUI, including history for obsolete NDCs
    /rxcui/{id}/related.json?tty=   RxCUI -> ingredient (IN) / multiple ingredient (MIN)
    /rxcui/{id}/historystatus.json  remapped concepts for retired RxCUIs
    /rxclass/class/byRxcui.json     RxCUI -> ATC class (relaSource=ATC or ATCPROD)
    /rxclass/allClasses.json        ATC class names for all levels
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from urllib.parse import urlencode

import requests

from src import config

BASE = "https://rxnav.nlm.nih.gov/REST"
CACHE_PATH = config.CACHE / "rxnav_cache.sqlite"

# NLM asks for no more than 20 requests/second per IP; stay below it.
MAX_REQUESTS_PER_SECOND = 15
MAX_RETRIES = 6


class RxNav:
    def __init__(self, cache_path=CACHE_PATH, rate=MAX_REQUESTS_PER_SECOND):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, body TEXT, fetched_at REAL)"
        )
        self._db_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._min_interval = 1.0 / rate
        self._next_slot = 0.0
        self._local = threading.local()
        self.n_network = 0
        self.n_cached = 0

    # -- plumbing ---------------------------------------------------------

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _wait_for_slot(self) -> None:
        """Global rate limiter shared by all threads."""
        with self._rate_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._min_interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def get(self, path: str, **params) -> dict:
        url = f"{BASE}{path}"
        if params:
            url += "?" + urlencode(sorted(params.items()))
        with self._db_lock:
            row = self._db.execute("SELECT body FROM cache WHERE url = ?", (url,)).fetchone()
        if row is not None:
            self.n_cached += 1
            return json.loads(row[0])

        for attempt in range(MAX_RETRIES):
            self._wait_for_slot()
            try:
                resp = self._session().get(url, timeout=60)
                if resp.status_code == 200:
                    body = resp.text
                    data = json.loads(body) if body.strip() else {}
                    with self._db_lock:
                        self._db.execute(
                            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                            (url, json.dumps(data), time.time()),
                        )
                        self._db.commit()
                    self.n_network += 1
                    return data
                if resp.status_code not in (429, 500, 502, 503, 504):
                    resp.raise_for_status()
            except (requests.ConnectionError, requests.Timeout, json.JSONDecodeError):
                pass
            time.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"RxNav request failed after {MAX_RETRIES} attempts: {url}")

    # -- endpoints --------------------------------------------------------

    def ndc_status(self, ndc: str) -> dict:
        return self.get("/ndcstatus.json", ndc=ndc).get("ndcStatus") or {}

    def related_ingredients(self, rxcui: str) -> dict[str, list[dict]]:
        """{'IN': [...], 'MIN': [...]} concept lists for a product RxCUI."""
        data = self.get(f"/rxcui/{rxcui}/related.json", tty="IN MIN")
        groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
        return {g["tty"]: g.get("conceptProperties") or [] for g in groups}

    def properties(self, rxcui: str) -> dict:
        """{'rxcui', 'name', 'tty', ...} for a concept; {} if retired/unknown."""
        return self.get(f"/rxcui/{rxcui}/properties.json").get("properties") or {}

    def related(self, rxcui: str, ttys: str) -> list[dict]:
        """Concepts of the given term types (space-separated) related to rxcui."""
        data = self.get(f"/rxcui/{rxcui}/related.json", tty=ttys)
        groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
        return [c for g in groups for c in (g.get("conceptProperties") or [])]

    def clinical_concept(self, product_rxcui: str) -> dict:
        """The generic clinical drug (SCD, or GPCK for packs) for a product RxCUI.

        SCD = ingredient + strength + dose form without a brand. A branded
        product (SBD/BPCK) maps to its SCD/GPCK; a retired RxCUI follows its
        remap. Used for equivalence groups and for generic-competition
        evidence (D-011, D-019).
        """
        props = self.properties(product_rxcui)
        if not props:
            remapped = ((self.history_status(product_rxcui).get("derivedConcepts") or {})
                        .get("remappedConcept")) or []
            for r in sorted(remapped, key=lambda r: r.get("remappedActive") != "YES"):
                if r.get("remappedRxCui"):
                    props = self.properties(r["remappedRxCui"])
                    if props:
                        break
        tty = props.get("tty")
        if tty in ("SCD", "GPCK"):
            return {"clinical_rxcui": props["rxcui"], "clinical_name": props["name"],
                    "clinical_tty": tty, "product_tty": tty}
        if tty in ("SBD", "BPCK"):
            want = "SCD" if tty == "SBD" else "GPCK"
            rel = sorted((c for c in self.related(props["rxcui"], "SCD GPCK") if c.get("tty") == want),
                         key=lambda c: c["rxcui"])
            if rel:
                return {"clinical_rxcui": rel[0]["rxcui"], "clinical_name": rel[0]["name"],
                        "clinical_tty": want, "product_tty": tty}
        return {"clinical_rxcui": None, "clinical_name": None, "clinical_tty": None, "product_tty": tty}

    def history_status(self, rxcui: str) -> dict:
        return self.get(f"/rxcui/{rxcui}/historystatus.json").get("rxcuiStatusHistory") or {}

    def classes(self, rxcui: str, rela_source: str) -> list[dict]:
        """RxClass ATC rows: [{'minConcept': {...}, 'rxclassMinConceptItem': {...}}]."""
        data = self.get("/rxclass/class/byRxcui.json", rxcui=rxcui, relaSource=rela_source)
        return (data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []

    def atc_class_names(self) -> dict[str, str]:
        data = self.get("/rxclass/allClasses.json", classTypes="ATC1-4")
        rows = (data.get("rxclassMinConceptList") or {}).get("rxclassMinConcept") or []
        return {r["classId"]: r["className"] for r in rows}
