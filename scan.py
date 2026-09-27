#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

API = "https://api.hh.ru"
TZ = ZoneInfo("Europe/Moscow")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("HH_BRIDGE_CONFIG", ROOT / "config" / "queries.json"))
OUTPUT_DIR = ROOT / "output"
VACANCIES_DIR = OUTPUT_DIR / "vacancies"
INDEX_PATH = OUTPUT_DIR / "index.json"

TOKEN = os.getenv("HH_ACCESS_TOKEN")
USER_AGENT = os.getenv("HH_USER_AGENT")

if not TOKEN:
    raise SystemExit("Missing HH_ACCESS_TOKEN")
if not USER_AGENT:
    raise SystemExit("Missing HH_USER_AGENT")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "HH-User-Agent": USER_AGENT,
    "Accept": "application/json",
})

def api_get(path: str, params=None, attempts: int = 5):
    url = f"{API}{path}"
    for attempt in range(1, attempts + 1):
        r = session.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()

        # Explicit failure for CAPTCHA/authorization problems.
        if r.status_code in (400, 401, 403):
            body = r.text[:1000]
            raise RuntimeError(f"HH API {r.status_code} for {url}: {body}")

        if r.status_code == 404:
            return None

        if r.status_code == 429 or 500 <= r.status_code < 600:
            if attempt == attempts:
                raise RuntimeError(f"HH API {r.status_code} after retries for {url}: {r.text[:1000]}")
            time.sleep(min(2 ** attempt, 20))
            continue

        raise RuntimeError(f"HH API unexpected {r.status_code} for {url}: {r.text[:1000]}")

def clean_html(value):
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))

def semantic_payload(v):
    # Fields that materially affect our vacancy scoring.
    return {
        "id": v.get("id"),
        "name": v.get("name"),
        "employer": (v.get("employer") or {}).get("name"),
        "area": (v.get("area") or {}).get("name"),
        "experience": v.get("experience"),
        "employment": v.get("employment"),
        "schedule": v.get("schedule"),
        "work_format": v.get("work_format"),
        "working_hours": v.get("working_hours"),
        "professional_roles": v.get("professional_roles"),
        "key_skills": v.get("key_skills"),
        "description_text": clean_html(v.get("description")),
    }

def sha256_json(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def compact_vacancy(v, source_queries):
    semantic = semantic_payload(v)
    return {
        "id": v.get("id"),
        "name": v.get("name"),
        "employer": v.get("employer"),
        "area": v.get("area"),
        "salary": v.get("salary"),
        "salary_range": v.get("salary_range"),
        "experience": v.get("experience"),
        "employment": v.get("employment"),
        "employment_form": v.get("employment_form"),
        "schedule": v.get("schedule"),
        "work_format": v.get("work_format"),
        "working_hours": v.get("working_hours"),
        "professional_roles": v.get("professional_roles"),
        "key_skills": v.get("key_skills"),
        "description_text": semantic["description_text"],
        "published_at": v.get("published_at"),
        "created_at": v.get("created_at"),
        "alternate_url": v.get("alternate_url"),
        "apply_alternate_url": v.get("apply_alternate_url"),
        "source_queries": sorted(source_queries),
        "semantic_hash": sha256_json(semantic),
    }

def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    days = int(cfg.get("rolling_days", 14))
    per_page = min(int(cfg.get("per_page", 100)), 100)
    areas = cfg.get("areas") or []

    now = datetime.now(TZ).replace(microsecond=0)
    date_from = now - timedelta(days=days)

    found = {}
    raw_hits = 0
    search_stats = []

    for q in cfg["queries"]:
        params = {
            "text": q["text"],
            "date_from": date_from.isoformat(),
            "date_to": now.isoformat(),
            "per_page": per_page,
            "page": 0,
            "order_by": "publication_time",
        }
        if q.get("search_field"):
            params["search_field"] = q["search_field"]
        if areas:
            params["area"] = areas

        first = api_get("/vacancies", params=params)
        pages = min(int(first.get("pages", 0)), 20)  # HH search window limit: 2000 items.
        pages = max(pages, 1)

        query_hits = 0
        for page in range(pages):
            data = first if page == 0 else api_get("/vacancies", params={**params, "page": page})
            items = data.get("items", [])
            raw_hits += len(items)
            query_hits += len(items)

            for item in items:
                vid = str(item["id"])
                rec = found.setdefault(vid, {"source_queries": set()})
                rec["source_queries"].add(q["text"])

            # Small pause so we behave like a polite client, a rare internet tradition.
            time.sleep(0.08)

        search_stats.append({
            "query": q["text"],
            "search_field": q.get("search_field"),
            "hits": query_hits,
            "pages": pages,
        })

    VACANCIES_DIR.mkdir(parents=True, exist_ok=True)

    entries = []
    failed_details = []

    for i, (vid, meta) in enumerate(sorted(found.items(), key=lambda kv: int(kv[0])), start=1):
        try:
            full = api_get(f"/vacancies/{vid}")
            if full is None:
                continue

            compact = compact_vacancy(full, meta["source_queries"])
            out = VACANCIES_DIR / f"{vid}.json"

            previous_hash = None
            if out.exists():
                try:
                    previous_hash = json.loads(out.read_text(encoding="utf-8")).get("semantic_hash")
                except Exception:
                    pass

            compact["changed_since_previous_scan"] = bool(
                previous_hash and previous_hash != compact["semantic_hash"]
            )

            out.write_text(
                json.dumps(compact, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            entries.append({
                "id": compact["id"],
                "name": compact["name"],
                "employer": (compact.get("employer") or {}).get("name"),
                "area": (compact.get("area") or {}).get("name"),
                "published_at": compact.get("published_at"),
                "alternate_url": compact.get("alternate_url"),
                "semantic_hash": compact["semantic_hash"],
                "changed_since_previous_scan": compact["changed_since_previous_scan"],
                "source_queries": compact["source_queries"],
                "detail_file": f"output/vacancies/{vid}.json",
            })
        except Exception as e:
            failed_details.append({"id": vid, "error": str(e)})

        if i % 25 == 0:
            time.sleep(0.5)

    entries.sort(key=lambda x: x.get("published_at") or "", reverse=True)

    index = {
        "generated_at": now.isoformat(),
        "rolling_days": days,
        "date_from": date_from.isoformat(),
        "date_to": now.isoformat(),
        "areas": areas,
        "queries_count": len(cfg["queries"]),
        "raw_hits": raw_hits,
        "unique_vacancy_ids": len(found),
        "detail_ok": len(entries),
        "detail_failed": len(failed_details),
        "search_stats": search_stats,
        "failed_details": failed_details,
        "vacancies": entries,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"HH scan complete: raw_hits={raw_hits}, "
        f"unique={len(found)}, detail_ok={len(entries)}, "
        f"detail_failed={len(failed_details)}"
    )

if __name__ == "__main__":
    main()
