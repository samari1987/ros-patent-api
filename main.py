from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from datetime import datetime
import requests

app = FastAPI(
    title="RosPatent API",
    version="0.2.0",
    description="Простой мост к поиску патентов через searchplatform.rospatent.gov.ru",
)

# -----------------------
# Константы и настройки
# -----------------------

ROS_ENDPOINT = "https://searchplatform.rospatent.gov.ru/search"
DEFAULT_DATASETS = [
    "ru_till_1994",
    "ru_since_1994",
    "cis",
    "dsgn_ru",
    "ap",
    "cn",
    "ch",
    "au",
    "gb",
    "kr",
    "ca",
    "at",
    "de",
    "es",
    "fr",
    "jp",
    "us",
    "wo",
    "pctmin",
    "pctapp",
    "pctpub",
    "smallfond",
]

HEADERS = {
    # минимальный набор, чтобы Роспатент тебя не отбрасывал как бота с пустым UA.
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (compatible; RosPatentBot/0.2; +https://example.invalid)",
    "Origin": "https://searchplatform.rospatent.gov.ru",
    "Referer": "https://searchplatform.rospatent.gov.ru/",
}


# -----------------------
# Модели ответа наружу
# -----------------------

class PatentItem(BaseModel):
    publicationNumber: Optional[str] = None
    kindCode: Optional[str] = None
    country: Optional[str] = None
    publicationDate: Optional[str] = None  # YYYY-MM-DD
    titleOriginal: Optional[str] = None
    titleRu: Optional[str] = None
    abstractOriginal: Optional[str] = None
    abstractRu: Optional[str] = None
    ipc: Optional[List[str]] = None


class SearchResponse(BaseModel):
    total: int
    page: int
    size: int
    nextPage: Optional[int]
    items: List[PatentItem]


# -----------------------
# Вспомогательные функции
# -----------------------

def _fmt_date(date_str: Optional[str]) -> Optional[str]:
    """
    Превращаем строки типа "2020.03.31" или "2025-09-23" в "YYYY-MM-DD".
    Если не получается — вернем None.
    """
    if not date_str:
        return None
    ds = date_str.strip()

    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%d.%m.%Y", "%Y%m%d", "%d.%m.%y"):
        try:
            dt = datetime.strptime(ds, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


def _safe_get(d: Dict[str, Any], *path, default=None):
    """
    Безопасно достать поле по цепочке ключей.
    """
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def _query_rospatent(query: str, offset: int, limit: int) -> Dict[str, Any]:
    """
    Делаем POST на searchplatform.rospatent.gov.ru/search
    Возвращаем уже распарсенный json (dict).
    Если упадёт — бросим Exception, который потом поймаем в /search.
    """

    payload = {
        "qn": query,
        "offset": offset,
        "limit": limit,
        "sort": "relevance",
        "preffered_lang": "ru",
        "countStatistics": True,
        "include_facets": 0,
        "highlight": {
            "profiles": ["_searchquery_"]
        },
        "datasets": DEFAULT_DATASETS,
        "pre_tag": "<span style='background: yellow' class=\"marked-element\">",
        "post_tag": "</span>"
    }

    r = requests.post(
        ROS_ENDPOINT,
        json=payload,
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()  # если 4xx/5xx — бросит исключение
    return r.json()


def _normalize_hit(hit: Dict[str, Any]) -> PatentItem:
    """
    Берём один элемент из "hits" и приводим к аккуратной форме PatentItem.
    Структура хита из Роспатента примерно такая:
    {
      "common": {...},
      "biblio": {...},
      ...
    }
    Нам интересны:
      publishing_office, document_number, kind, publication_date,
      title (ru/en), abstract (ru/en), classification.ipc
    """

    common = _safe_get(hit, "common", default={})
    biblio = _safe_get(hit, "biblio", default={})
    ru_block = _safe_get(biblio, "ru", default={})
    en_block = _safe_get(biblio, "en", default={})

    # Публикационный номер
    country = _safe_get(common, "publishing_office", default="").strip() or None
    docnum = _safe_get(common, "document_number", default="").strip()
    kind = _safe_get(common, "kind", default="").strip()
    pub_number = None
    if docnum:
        # обычно publishing_office похож на "RU", "WO", "US" и т.д.
        # соберём как "RU000000" + kind
        if country:
            pub_number = f"{country}{docnum}{kind}"
        else:
            pub_number = f"{docnum}{kind}" if kind else docnum

    # Дата публикации
    raw_pub_date = _safe_get(common, "publication_date", default=None)
    pub_date = _fmt_date(raw_pub_date)

    # Заголовки
    title_ru = _safe_get(ru_block, "title", default=None)
    title_en = _safe_get(en_block, "title", default=None)
    # В некоторых записях "biblio" может содержать только "title" (строка), без вложенных lang-блоков.
    if not title_ru and isinstance(biblio.get("title"), str):
        title_ru = biblio.get("title")

    # Аннотации
    abstr_ru = _safe_get(ru_block, "abstract", default=None)
    abstr_en = _safe_get(en_block, "abstract", default=None)
    if not abstr_ru and isinstance(biblio.get("abstract"), str):
        abstr_ru = biblio.get("abstract")

    # IPC
    ipc_list = []
    classification = _safe_get(common, "classification", default={})
    ipc_entries = classification.get("ipc") if isinstance(classification.get("ipc"), list) else []
    for entry in ipc_entries:
        # entry может быть dict с кусками типа "main_group", "subgroup" и т.д.
        # простейший склей вариант:
        main_group = entry.get("main_group")
        subgroup = entry.get("subgroup")
        fullcode = entry.get("fullname")
        if fullcode:
            ipc_list.append(fullcode)
        else:
            bits = [main_group, subgroup]
            bits = [b for b in bits if b]
            if bits:
                ipc_list.append(" ".join(bits))

    return PatentItem(
        publicationNumber=pub_number,
        kindCode=kind or None,
        country=country or None,
        publicationDate=pub_date,
        titleOriginal=title_en or title_ru,
        titleRu=title_ru,
        abstractOriginal=abstr_en or abstr_ru,
        abstractRu=abstr_ru,
        ipc=ipc_list if ipc_list else None,
    )


# -----------------------
# Эндпоинты
# -----------------------

@app.get("/status")
def status():
    return {
        "status": "ok",
        "service": "ros",
        "time": datetime.utcnow().isoformat()
    }


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., description="поисковый запрос, например 'солнечное опреснение'"),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=25),
):
    """
    Основной эндпоинт.
    Делает запрос к Роспатенту с оффсетом и лимитом.
    Возвращает нормализованный список патентов.
    """

    try:
        offset = (page - 1) * size
        raw = _query_rospatent(q, offset, size)

        hits = raw.get("hits", [])
        total = raw.get("total", len(hits))

        cleaned_items = [_normalize_hit(h) for h in hits]

        # вычисляем номер следующей страницы
        next_page = page + 1 if (offset + size) < total else None

        return SearchResponse(
            total=total,
            page=page,
            size=size,
            nextPage=next_page,
            items=cleaned_items,
        )

    except Exception as e:
        # чтобы не падать 500-кой без объяснений, мы вернем "псевдоответ" с пустыми данными,
        # но в логе Render будет стек ошибки
        print("ERROR in /search:", repr(e))
        return SearchResponse(
            total=0,
            page=page,
            size=size,
            nextPage=None,
            items=[],
        )
