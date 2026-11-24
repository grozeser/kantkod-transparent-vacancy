import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Batch:
    vacancies: list[dict]
    pages: int
    diagnostics: list[str]


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Не удалось прочитать JSON {path.name}: {exc}") from exc


def parse_response(payload, label: str) -> tuple[list[dict], int | None]:
    if not isinstance(payload, dict):
        raise InputError(f"{label}: в корне JSON должен быть объект")
    if str(payload.get("status", 200)) != "200":
        raise InputError(f"{label}: источник вернул статус {payload['status']!r}")
    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("vacancies"), list):
        raise InputError(f"{label}: results.vacancies должен быть массивом")

    rows = []
    for index, wrapper in enumerate(results["vacancies"]):
        path = f"{label}: results.vacancies[{index}].vacancy"
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("vacancy"), dict):
            raise InputError(f"{path} должен быть объектом")
        vacancy = wrapper["vacancy"]
        identity = vacancy.get("id")
        if type(identity) not in (str, int) or not str(identity).strip():
            raise InputError(f"{path}.id должен содержать ID вакансии")
        rows.append({**vacancy, "id": str(identity).strip()})

    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        raise InputError(f"{label}: meta должен быть объектом")
    total = meta.get("total")
    if total is not None:
        if type(total) not in (str, int) or not str(total).isdigit():
            raise InputError(f"{label}: meta.total должен быть неотрицательным целым")
        total = int(total)
    return rows, total


def collect_unique(rows, maximum=None):
    unique = {}
    duplicates = 0
    for vacancy in rows:
        identity = vacancy["id"]
        if identity in unique:
            duplicates += 1
            continue
        unique[identity] = vacancy
        if maximum is not None and len(unique) == maximum:
            break
    return list(unique.values()), duplicates


def load_file(path: Path) -> Batch:
    rows, _ = parse_response(read_json(path), path.name)
    vacancies, duplicates = collect_unique(rows)
    diagnostics = [f"Повторных записей по ID: {duplicates}"] if duplicates else []
    return Batch(vacancies, 1, diagnostics)


def page_url(url: str, limit: int, offset: int) -> str:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InputError("Некорректный URL источника") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise InputError("URL источника должен быть HTTP(S) и не содержать учётных данных")
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"limit", "offset"}]
    query.extend((("limit", str(limit)), ("offset", str(offset))))
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def fetch_page(url: str, timeout: float, label: str):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "KantkodVacancyReview/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(20_000_001)
        if len(body) > 20_000_000:
            raise ValueError("ответ превышает 20 МБ")
        return json.loads(body)
    except (URLError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise InputError(f"{label}: не удалось получить JSON от API ({exc})") from exc


def load_api(url: str, maximum: int, page_size: int, offset_unit: str, timeout: float) -> Batch:
    if maximum < 1 or not 1 <= page_size <= 100 or timeout <= 0 or offset_unit not in {"page", "item"}:
        raise InputError("Проверьте лимит, размер страницы (1–100), единицу смещения и таймаут")
    # A fixed page size is required when the source treats offset as a page index.
    limit = min(maximum, page_size)
    pages = 0
    seen_rows = 0
    page_ids = set()
    unique = {}
    duplicates = 0
    while len(unique) < maximum:
        label = f"Страница {pages + 1}"
        offset = pages if offset_unit == "page" else seen_rows
        payload = fetch_page(page_url(url, limit, offset), timeout, label)
        rows, total = parse_response(payload, label)
        pages += 1
        if not rows:
            if total is not None and seen_rows < total:
                raise InputError(f"{label}: пустая страница до заявленного конца выгрузки")
            break
        signature = tuple(sorted({row["id"] for row in rows}))
        if signature in page_ids:
            raise InputError(f"{label}: API повторил страницу, проверьте параметр offset")
        page_ids.add(signature)
        seen_rows += len(rows)
        for row in rows:
            if row["id"] in unique:
                duplicates += 1
            elif len(unique) < maximum:
                unique[row["id"]] = row
        if total is not None and seen_rows >= total:
            break
        if total is None and len(rows) < limit:
            break
        if pages >= maximum + 1:
            raise InputError("API не завершил выгрузку в пределах ожидаемого числа страниц")
    diagnostics = [f"Повторных записей по ID: {duplicates}"] if duplicates else []
    return Batch(list(unique.values()), pages, diagnostics)
