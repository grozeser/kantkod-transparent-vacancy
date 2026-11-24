from time import perf_counter

from .analysis import analyse_group
from .model import ClarityModel


BASE = {
    "job-name": "Инженер по эксплуатации зданий",
    "duty": "Осматривать помещения, регистрировать дефекты и составлять задания на ремонт.",
    "requirements": "Диплом строительного колледжа и знание техники безопасности.",
    "salary_min": 46000, "salary_max": 52000,
    "schedule": "5/2, с 09:00 до 17:30", "employment": "Полная занятость",
    "addresses": {"address": [{"location": "Калининград, улица Испытательная, дом 8"}]},
}

TUNING = [
    ("duties-vague", {"duty": "Участвовать в повседневных процессах подразделения."}, [("duties", "ambiguous")]),
    ("duties-clear", {"duty": "Оформлять акты осмотра и обновлять журнал дефектов."}, []),
    ("requirements-vague", {"requirements": "Ищем энергичного человека, который умеет работать на результат."}, [("requirements", "ambiguous")]),
    ("requirements-clear", {"requirements": "Навыки сметного расчёта, чтение чертежей и образование ПГС."}, []),
    ("schedule-vague", {"schedule": "Режим работы определяется руководителем отдела."}, [("schedule", "ambiguous")]),
    ("schedule-clear", {"schedule": "Гибкий режим, обязательные часы с 10:00 до 14:00."}, []),
    ("pay-vague", {"salary_min": 0, "salary_max": 0, "salary": "О размере выплат договоримся позже."}, [("pay", "ambiguous")]),
    ("pay-clear", {"salary_min": 0, "salary_max": 0, "salary": "Ставка 520 ₽ в час."}, []),
    ("employment-vague", {"employment": "Продолжительность занятости обсуждается отдельно."}, [("employment", "ambiguous")]),
    ("workplace-vague", {"addresses": None, "description": "Адрес работы назовут после заключения договора."}, [("workplace", "ambiguous")]),
]

HOLDOUT = [
    ("duties-open-ended", {"duty": "Выполнять задачи, которые назначат в ходе работы."}, [("duties", "ambiguous")]),
    ("duties-other-requests", {"duty": "Обеспечивать повседневную деятельность подразделения и содействовать коллегам."}, [("duties", "ambiguous")]),
    ("duties-clear-help", {"duty": "Помогать сотрудникам: создавать учётные записи, назначать права и выдавать технику."}, []),
    ("requirements-personality", {"requirements": "Ответственность, желание развиваться и позитивный настрой."}, [("requirements", "ambiguous")]),
    ("requirements-clear", {"requirements": "Знание чертежей AutoCAD, действующий допуск к высотным работам."}, []),
    ("schedule-open", {"schedule": "Рабочие часы будут согласованы после оформления."}, [("schedule", "ambiguous")]),
    ("schedule-mentioned", {"schedule": "Гибкий график. Обязательные часы 10:00–14:00."}, []),
    ("schedule-in-other-field", {"schedule": "Сменный график", "description": "Рабочий график: 2/2, 07:00–19:00."}, []),
    ("schedule-conditional", {"schedule": "Гибкий режим", "description": "Пятидневная рабочая неделя с 10:00 до 19:00. Возможен гибкий режим."}, [("schedule", "ambiguous")]),
    ("schedule-conflict", {"schedule": "Сменный график 2/2", "description": "Рабочий график 5/2 каждую неделю."}, [("schedule", "conflict")]),
    ("pay-open", {"salary_min": 0, "salary_max": 0, "salary": "Сумма оплаты обсуждается индивидуально с кандидатом."}, [("pay", "ambiguous")]),
    ("pay-in-other-field", {"salary_min": 0, "salary_max": 0, "description": "Гарантированный оклад — 59 000 ₽."}, []),
    ("pay-boundary", {"salary_min": 43000, "salary_max": 0}, []),
    ("pay-numeric-conflict", {"salary_min": 69000, "salary_max": 51000}, [("pay", "conflict")]),
    ("remote-with-address", {"description": "Работа полностью дистанционная из любого региона."}, []),
    ("remote-conflict", {"description": "Полностью удалённая работа из любого города.", "requirement": {"qualification": "Обязательное присутствие в офисе каждый день."}}, [("workplace", "conflict")]),
    ("address-not-specified", {"addresses": None, "description": "Точный адрес рабочего места уточняется на собеседовании."}, [("workplace", "ambiguous")]),
    ("employment-options", {"employment": "Полная или частичная занятость по выбору кандидата."}, []),
    ("employment-conflict", {"description": "Обязательна частичная занятость, 20 часов в неделю."}, [("employment", "conflict")]),
    ("title-missing", {"job-name": None}, [("title", "missing")]),
]


def evaluate_model(dataset):
    entries = TUNING if dataset == "tuning" else HOLDOUT
    vacancies = [{**BASE, **changes, "id": identity} for identity, changes, _ in entries]
    expected = {identity: set(values) for identity, _, values in entries}
    model = ClarityModel()
    result = {"dataset": dataset, "cases": len(entries)}
    for name, classifier in (("rules", None), ("rules_with_ml", model)):
        start = perf_counter()
        records = analyse_group(vacancies, classifier)
        elapsed = round((perf_counter() - start) * 1000, 1)
        summary = {"true_positives": 0, "false_positives": 0, "false_negatives": 0, "analysis_ms": elapsed, "errors": [], "confirmed_ml_examples": []}
        for row in records:
            vacancy = row["record"]
            identity = vacancy["id"]
            actual = {(item["topic"], item["type"]) for item in vacancy["issues"]}
            missing, extra = expected[identity] - actual, actual - expected[identity]
            summary["true_positives"] += len(actual & expected[identity])
            summary["false_positives"] += len(extra)
            summary["false_negatives"] += len(missing)
            if missing or extra:
                summary["errors"].append({"case": identity, "missing": sorted(missing), "extra": sorted(extra)})
            for item in vacancy["issues"]:
                if item["method"] == "ml" and (item["topic"], item["type"]) in expected[identity]:
                    summary["confirmed_ml_examples"].append({"case": identity, "evidence": item["evidence"], "recommendation": item["recommendation"]})
        result[name] = summary
    return result
