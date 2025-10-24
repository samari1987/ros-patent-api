# main.py — RosPatent API proxy (для Маши)
# Рабочая версия: FastAPI, POST-запрос к https://searchplatform.rospatent.gov.ru

from fastapi import FastAPI, Query
import requests
from typing import Optional
from datetime import datetime

app = FastAPI(
    title="RosPatent API",
    version="0.1.0",
    description="API-шлюз для поиска патентов через поисковую платформу Роспатента"
)

def _query_rospatent(q: str, offset: int, limit: int):
    url = "https://searchplatform.rospatent.gov.ru/search"
    payload = {
        "qn": q,
        "offset": offset,
        "limit": limit,
        "sort": "relevance",
        "preffered_lang": "ru",
        "highlight": {"profiles": ["_searchquery_"]},
        "datasets": [
            "ru_till_1994", "ru_since_1994", "cis", "dsgn_ru",
            "ap", "cn", "ch", "au", "gb", "kr", "ca", "at",
            "de", "es", "fr", "jp", "sg", "us", "wo", "ea"
        ],
        "countStatistics": True,
        "include_facets": 0,
        "pre_tag": "<span style='background: yellow' class=\"marked-element\">",
        "post_tag": "</span>"
    }
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://searchplatform.rospatent.gov.ru",
        "Referer": "https://searchplatform.rospatent.gov.ru/",
        "User-Agent": "Mozilla/5.0 (compatible; RosPatentBot/0.1)"
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def _normalize(hit):
    common = hit.get("common", {})
    biblio = hit.get("biblio", {}).get("ru", {})
    return {
        "publicationNumber": f"{common.get('publishing_office','')}{common.get('document_number','')}{common.get('kind','')}",
        "publicationDate": common.get("publication_date"),
        "titleRu": biblio.get("title") or biblio.get("name"),
        "abstractRu": biblio.get("abstract") or biblio.get("annotation"),
        "office": common.get("publishing_office"),
        "applicationNumber": common.get("application", {}).get("number"),
        "ipc": [i.get("fullname") for i in common.get("classification", {}).get("ipc", []) if isinstance(i, dict)],
    }

@app.get("/status")
def status():
    return {"status": "ok", "service": "ros", "time": datetime.utcnow().isoformat()}

@app.get("/search")
def search(
    q: str = Query(..., description="поисковый запрос, например 'солнечное опреснение'"),
    page: int = 1,
    size: int = 10
):
    offset = (page - 1) * size
    data = _query_rospatent(q, offset, size)
    hits = data.get("hits", [])
    items = [_normalize(h) for h in hits]
    total = data.get("total", len(items))
    next_page = page + 1 if offset + size < total else None
    return {
        "total": total,
        "page": page,
        "size": size,
        "nextPage": next_page,
        "items": items
    }
