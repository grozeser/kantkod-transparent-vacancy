import json
import re
from copy import deepcopy
from functools import lru_cache

from .analysis import analyse_group
from .model import ClarityModel


WEEK = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


@lru_cache(maxsize=1)
def draft_model():
    return ClarityModel()


def draft_text(mode, days):
    if mode == "remote":
        return "Работа полностью дистанционная."
    if mode == "office":
        return "Работа только в офисе."
    if mode == "hybrid" and isinstance(days, list) and days and all(type(day) is int and 0 <= day < len(WEEK) for day in days):
        chosen = sorted(set(days))
        if len(chosen) < len(WEEK):
            return "Гибридный формат. Дни присутствия в офисе: " + ", ".join(WEEK[index] for index in chosen) + "."
    raise ValueError("Выберите формат работы. Для гибрида укажите дни присутствия в офисе.")


def preview_vacancy(db, channel, request):
    identity, selection = request.get("id"), request.get("fields")
    if not isinstance(identity, str) or not isinstance(selection, list) or any(type(item) is not int for item in selection):
        raise ValueError("Выберите вакансию и поля для правки.")
    row = db.execute("SELECT result FROM cache WHERE channel=? AND vacancy_id=?", (channel, identity)).fetchone()
    cached = json.loads(row["result"]) if row else {}
    if "draft_source" not in cached:
        raise ValueError("Обновите отчёт, чтобы открыть черновик для этой вакансии.")
    before = cached["record"]["issues"]
    finding = next((item for item in before if item["topic"] == "workplace" and item["type"] == "conflict"), None)
    if not finding or finding["evidence"] != request.get("evidence"):
        raise ValueError("Исходные условия изменились. Обновите отчёт и откройте черновик снова.")
    return analyse_draft(cached["draft_source"], before, selection, request.get("mode"), request.get("days"))


def analyse_draft(source, before, selection, mode, days):
    finding = next((item for item in before if item["topic"] == "workplace" and item["type"] == "conflict"), None)
    if finding is None:
        raise ValueError("В текущей версии вакансии нет конфликта формата работы.")
    evidence = finding["evidence"]
    if not selection or any(index < 0 or index >= len(evidence) for index in selection):
        raise ValueError("Выберите хотя бы одно из полей конфликта.")
    replacement = draft_text(mode, days)
    draft, changes = deepcopy(source), {}
    for index in sorted(set(selection)):
        item = evidence[index]
        path, quote = item["field"], item["quote"]
        tokens = re.findall(r"[^.\[\]]+", path)
        container = draft
        for token in tokens[:-1]:
            container = container[int(token) if isinstance(container, list) else token]
        key = int(tokens[-1]) if isinstance(container, list) else tokens[-1]
        original = container[key]
        if not isinstance(quote, str) or not isinstance(original, str) or original.count(quote) != 1:
            raise ValueError("Для поля «" + path + "» нельзя однозначно определить место правки.")
        # The evidence may start inside markup. Keep any tags at its boundaries.
        opening = re.match(r"(?:<[^>]+>\s*)*", quote)[0]
        closing = re.search(r"(?:\s*</[^>]+>)+$", quote)
        container[key] = original.replace(quote, opening + replacement + (closing[0] if closing else ""), 1)
        change = changes.setdefault(path, {"field": path, "before": original})
        change["after"] = container[key]

    result = analyse_group([draft], draft_model())[0]["record"]["issues"]
    result_keys = {(item["topic"], item["type"]) for item in result}
    return {
        "changes": list(changes.values()),
        "closed": [item for item in before if (item["topic"], item["type"]) not in result_keys],
        "remaining": result,
        "workplace_conflict": ("workplace", "conflict") in result_keys,
    }
