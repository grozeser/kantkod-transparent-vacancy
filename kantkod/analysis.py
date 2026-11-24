import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


TOPICS = {
    "title": "Должность", "duties": "Обязанности", "requirements": "Требования",
    "pay": "Оплата", "schedule": "График", "workplace": "Место работы",
    "employment": "Тип занятости",
}
RULES_VERSION = "1.0"
QUESTIONS = {
    "title": "Как называется должность полностью и к какому направлению она относится?",
    "duties": "Какие 3–5 задач сотрудник выполняет регулярно? Укажите действия и результаты работы.",
    "requirements": "Какие навыки и какой опыт обязательны, а чему можно научиться после трудоустройства?",
    "pay": "Какую сумму или вилку получит работник, за какой период и до или после удержаний?",
    "schedule": "В какие дни и часы нужно работать? Если есть смены, какой у них цикл?",
    "workplace": "Какой формат действует: офис, гибрид или удалёнка? Если нужен офис, укажите адрес и дни присутствия.",
    "employment": "Занятость полная или частичная? Если частичная, сколько часов в неделю нужно работать?",
}
MISSING = {
    "title": "Название должности", "duties": "Перечень обязанностей", "requirements": "Требования к кандидату",
    "pay": "Сумма или вилка оплаты", "schedule": "Рабочий график", "workplace": "Место или формат работы",
    "employment": "Тип занятости",
}
VAGUE = {
    "title": "Название не уточняет направление работы.",
    "duties": "Формулировка не объясняет, какие задачи нужно выполнять на практике.",
    "requirements": "Формулировка не отделяет обязательные требования от общих пожеланий.",
    "pay": "По формулировке нельзя определить сумму или вилку оплаты.",
    "schedule": "Формулировка оставляет открытым вопрос о рабочих днях, часах или сменах.",
    "workplace": "Формулировка не позволяет определить место или формат работы.",
    "employment": "Формулировка не объясняет объём занятости.",
}
EMPTY = {"", "нет данных", "не указано", "не указан", "не указана", "не заполнено", "null", "none", "-"}
TIME_RANGE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(?:-|до)\s*(\d{1,2})[:.](\d{2})\b")
CYCLE = re.compile(r"\b([1-7])\s*/\s*([1-7])\b")
EXPERIENCE = re.compile(r"(?:(?:опыт|стаж)\D{0,25})?(\d+)\s*(?:лет|года|год)(?:\s+опыт\w*)?")
AMOUNT = re.compile(r"\d{1,3}(?: \d{3})+|\d+(?:[.,]\d+)?")
CONDITIONAL = re.compile(r"\b(?:возмож\w*|либо|или|на выбор|по согласованию|в зависимости)\b")


def clean(value):
    value = re.sub(r"<[^>]*>", " ", str(value))
    return " ".join(html.unescape(value).split())


def normal(value):
    return clean(value).casefold().replace("ё", "е").translate(str.maketrans("–—−", "---"))


@dataclass(frozen=True)
class Field:
    path: str
    value: str | int | float

    @property
    def text(self):
        return normal(self.value)

    def evidence(self):
        return {"field": self.path, "quote": self.value}


def fields(data, *paths):
    result = []
    for path in paths:
        value = data
        for segment in path.split("."):
            value = value.get(segment) if isinstance(value, dict) else None
        if type(value) in (str, int, float) and normal(value) not in EMPTY:
            result.append(Field(path, value))
    return result


def unique_fields(items):
    return list({field.path: field for field in items}.values())


def issue(topic, kind, sources, problem, question=None, method="rules"):
    return {
        "topic": topic, "type": kind, "method": method,
        "evidence": [source.evidence() for source in sources],
        "problem": problem, "recommendation": question or QUESTIONS[topic],
    }


def missing(topic, path):
    return issue(topic, "missing", [Field(path, "")], f"{MISSING[topic]} отсутствует в предоставленных данных.")


def link_for(vacancy):
    for field in fields(vacancy, "vac_url", "url", "link"):
        if not isinstance(field.value, str) or any(ch.isspace() for ch in field.value):
            continue
        try:
            url = urlsplit(field.value)
            if url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password:
                return field.value
        except ValueError:
            continue
    return None


def number(value):
    if type(value) in (int, float):
        return value if 0 < value < float("inf") else None
    match = AMOUNT.fullmatch(normal(value))
    if match:
        amount = float(match[0].replace(" ", "").replace(",", "."))
        return amount if 0 < amount < float("inf") else None
    return None


def payment_in(text, structured=False):
    if not structured and not re.search(r"(?:руб|₽|зарплат|оплат|оклад|на руки)", text):
        return None
    match = re.search(r"(?:от\s*)?(\d[\d ]*\d|\d)(?:\s*(тыс\.?))?\s*(?:(?:-|до)\s*(\d[\d ]*\d|\d)(?:\s*(тыс\.?))?)?\s*(?:₽|руб\w*|р\.)", text)
    if not match and structured:
        match = re.fullmatch(r"(?:от\s*)?(\d[\d ]*\d|\d)(?:\s*(тыс\.?))?\s*(?:(?:-|до)\s*(\d[\d ]*\d|\d)(?:\s*(тыс\.?))?)?", text)
    if not match:
        return None
    low, low_thousands, high, high_thousands = match.groups()
    low = int(low.replace(" ", "")) * (1000 if low_thousands or (high_thousands and len(low.strip()) <= 3) else 1)
    high = int(high.replace(" ", "")) * (1000 if high_thousands else 1) if high else None
    if low <= 0 or high == 0:
        return None
    if high is None and re.search(r"\bдо\s*$", text[:match.start()]):
        return None, low
    upper = high if high is not None else (None if match[0].startswith("от") else low)
    return low, upper


def schedule_facts(text):
    cycles = {f"{match[1]}/{match[2]}" for match in CYCLE.finditer(text)}
    if re.search(r"пятиднев|понедельник\w*\s*-\s*пятниц", text):
        cycles.add("5/2")
    hours = {
        f"{int(match[1]):02}:{match[2]}–{int(match[3]):02}:{match[4]}"
        for match in TIME_RANGE.finditer(text)
        if int(match[1]) < 24 and int(match[3]) <= 24 and int(match[2]) < 60 and int(match[4]) < 60
    }
    return cycles, hours


def work_mode(text):
    if re.search(r"гибрид|частично (?:удален|дистанц)|удален\w* (?:или|и) офис|офис\w* (?:или|и) удален", text):
        return "hybrid" if " или " not in text else None
    if re.search(r"(?:не|без возможности)\s+(?:полностью\s+)?(?:удален|дистанц)", text):
        return None
    if re.search(r"удален\w*|дистанцион\w*", text):
        if re.search(r"(?:полностью\s+(?:удален|дистанц)|из любого (?:города|региона)|только удален|(?:работа|режим|формат)\s+(?:удален|дистанц)|(?:удален\w*|дистанцион\w*)\s+(?:работа|формат|режим))", text):
            return "remote_only"
        return "remote"
    if re.search(r"(?:(?:только|полностью)\s+в офис|обязательн\w*\s+(?:ежедневн\w*\s+)?присутствие\s+в офис|ежедневн\w*\s+(?:\w+\s+){0,3}в офис|офисный формат|работа в офисе)", text):
        return "office"
    return None


def employment_kind(text):
    full = text == "полная" or re.search(r"\bполн\w*\s+занятост|полную ставку|\b1[,.]0\s*ставк", text)
    partial = text == "частичная" or re.search(r"\b(?:частичн\w*|неполн\w*)\s+занятост|\b0[,.][1-9]\s*ставк", text)
    if bool(full) != bool(partial):
        return "full" if full else "part"
    return None


def addresses(vacancy):
    result = fields(vacancy, "address", "location", "work_address", "workplace", "work_format", "work-format")
    container = vacancy.get("addresses")
    entries = container.get("address") if isinstance(container, dict) else None
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                for field in fields(entry, "location", "address"):
                    result.append(Field(f"addresses.address[{index}].{field.path}", field.value))
            if isinstance(entry, str) and normal(entry) not in EMPTY:
                result.append(Field(f"addresses.address[{index}]", entry))
    return result


def diagnostics_for(vacancy):
    expected = {
        "company": (dict,), "requirement": (dict, str), "addresses": (dict,), "job-name": (str,),
        "schedule": (str,), "duty": (str,), "employment": (str,), "salary_min": (int, float, str),
        "salary_max": (int, float, str), "vac_url": (str,),
    }
    errors = []
    for path, types in expected.items():
        value = vacancy.get(path)
        if value is not None and type(value) not in types:
            errors.append(f"Вакансия {vacancy['id']}: поле vacancy.{path}, неожиданный тип {type(value).__name__}")
    address = vacancy.get("addresses")
    if isinstance(address, dict) and address.get("address") is not None and not isinstance(address["address"], list):
        errors.append(f"Вакансия {vacancy['id']}: поле vacancy.addresses.address, ожидался массив")
    return errors


def examples_for(topic, sources):
    return [(topic, field) for field in unique_fields(sources) if len(field.text) >= 8]


def specific_duties(sources):
    actions = re.compile(r"\b(?:регистрир|прием|веден|учет|подготов|состав|разработ|сбор|обнов|осмотр|оформ|свер|провер|созда|выда|обуч|диагност|настраив|обрабатыв|анализир|написан|коммуникац)\w*\s+(?!задач\b|поручен\w*|вопрос\w*|деятельност\w*|процесс\w*)[а-яa-z]{3,}")
    return len({match[0] for field in sources for match in actions.finditer(field.text)}) >= 2


def specific_requirements(sources):
    return any(re.search(r"\b(?:образование|диплом|допуск|удостоверение|сертификат|медицинская книжка)\b|\b(?:знани\w*|владени\w*|умени\w*)\s+(?!нужн\w*|требуем\w*|навык\w*|необходим\w*)[а-яa-z0-9]{2,}|\bопыт\s+(?:разработк|веден|подготовк|работы\s+(?:с|в)\s+)|\bопыт\s+не\s+(?:обязателен|требуется)", field.text) for field in sources)


def schedule_review(structured, prose):
    related = [f for f in prose if re.search(r"график|смен|рабоч\w*\s+(?:день|недел)|пятиднев|будн", f.text)]
    all_fields = unique_fields(structured + related)
    parsed = [(f, *schedule_facts(f.text)) for f in all_fields]
    cycles = sorted({value for _, values, _ in parsed for value in values})
    hours = sorted({value for _, _, values in parsed for value in values})
    flexible = [f for f in all_fields if re.search(r"гибк\w*\s+(?:график|режим)", f.text)]
    findings = []
    for index, (left, lc, lh) in enumerate(parsed):
        for right, rc, rh in parsed[index + 1:]:
            exclusive = (len(lc) == len(rc) == 1 and lc != rc) or (len(lh) == len(rh) == 1 and lh != rh)
            if exclusive and not any(CONDITIONAL.search(f.text) or "гибк" in f.text for f in (left, right)):
                findings.append(issue("schedule", "conflict", [left, right], "В полях указаны разные графики или часы работы."))
                break
        if findings:
            break

    if not findings and flexible:
        exception = next((f for f in flexible if CONDITIONAL.search(f.text) or "без уточнения" in f.text), None)
        if exception and (cycles or hours) and not re.search(r"согласу\w*\s+(?:с|заранее)|начало\s+с\s+\d|коридор|фиксированн\w*\s+час", exception.text):
            findings.append(issue("schedule", "ambiguous", [exception], "Указан обычный график и возможность гибкого режима. Условия гибкости не описаны.",
                                  "Кому доступен гибкий режим, какие часы обязательны и как согласуется остальное время?"))
    if not findings and not all_fields:
        findings.append(missing("schedule", "schedule"))
    options = " / ".join(cycles + hours)
    summary = options or " / ".join(sorted({f.text for f in all_fields}))
    if options and flexible:
        summary += " · гибкий режим"
    candidates = [] if findings or cycles or hours else examples_for("schedule", all_fields)
    return findings, candidates, summary


def pay_review(vacancy, prose):
    minimum = fields(vacancy, "salary_min", "salary.min")
    maximum = fields(vacancy, "salary_max", "salary.max")
    low_source = next((f for f in minimum if number(f.value) is not None), None)
    high_source = next((f for f in maximum if number(f.value) is not None), None)
    low = number(low_source.value) if low_source else None
    high = number(high_source.value) if high_source else None
    structured = fields(vacancy, "salary", "payment", "wage")
    prose = [f for f in prose if re.search(r"зарплат|оплат|оклад|вознагражден|₽|рубл", f.text)]
    known = [(f, payment_in(f.text, True)) for f in structured] + [(f, payment_in(f.text)) for f in prose]
    known = [(f, values) for f, values in known if values]
    stated = [f for f in (low_source, high_source) if f]
    if low is not None and high is not None and low > high:
        return [issue("pay", "conflict", stated, "Минимальная сумма больше максимальной.")], [], f"{low:g}–{high:g}"
    if stated:
        for field, (other_low, other_high) in known:
            if re.search(r"(?:налог|на руки|за час|за смену|в час|в день|преми|бонус|испытательн)", field.text):
                continue  # Different pay bases are not directly comparable to structured amounts.
            if (high is not None and other_low is not None and high < other_low) or (low is not None and other_high is not None and low > other_high):
                boundary = high_source if high is not None and other_low is not None and high < other_low else low_source
                return [issue("pay", "conflict", [boundary, field], "Сумма в тексте выходит за пределы указанной оплаты.")], [], " / ".join(f.text for f in stated + [field])
        bounds = f"{low:g}–{high:g}" if low is not None and high is not None else (f"от {low:g}" if low is not None else f"до {high:g}")
        return [], [], bounds
    if known:
        return [], [], " / ".join(sorted({f.text for f, _ in known}))
    qualitative = unique_fields(structured + prose)
    candidates = examples_for("pay", qualitative)
    if not candidates:
        return [missing("pay", "salary_min")], [], ""
    return [], candidates, " / ".join(sorted({f.text for f in qualitative}))


def workplace_review(address_fields, prose):
    prose = [f for f in prose if re.search(r"удален|дистанц|офис|рабоч\w*\s+мест|\bадрес\b|гибрид", f.text)]
    all_fields = unique_fields(address_fields + prose)
    modes = [(mode, f) for f in all_fields if (mode := work_mode(f.text))]
    remote = next((f for mode, f in modes if mode == "remote_only" and not CONDITIONAL.search(f.text)), None)
    office = next((f for mode, f in modes if mode == "office" and not CONDITIONAL.search(f.text)), None)
    if remote and office and remote.path != office.path:
        return [issue("workplace", "conflict", [remote, office], "Полная удалёнка несовместима с обязательным присутствием в офисе.")], [], f"{remote.text} / {office.text}"
    stated_address = next((f for f in address_fields if re.search(r"\b(?:ул|улица|проспект|дом|пер|шоссе|бульвар|площадь)\b|\d", f.text)), None)
    summary = " / ".join(sorted({f.text for f in address_fields + [f for _, f in modes]}))
    if stated_address or any(mode in {"remote", "remote_only"} for mode, _ in modes):
        return [], [], summary
    if not all_fields:
        return [missing("workplace", "addresses.address")], [], ""
    unresolved = next((f for f in prose if re.search(r"(?:адрес|формат|место).*уточня\w*.*собеседован", f.text)), None)
    if unresolved:
        return [issue("workplace", "ambiguous", [unresolved], "Точный адрес или формат предлагается узнать только на собеседовании.")], [], summary or unresolved.text
    return [], examples_for("workplace", prose), summary


def prepare(vacancy):
    description = fields(vacancy, "description", "job-description", "job_description", "additional-information", "additional_information", "conditions")
    title = fields(vacancy, "job-name", "job_name", "title", "name")
    duties = fields(vacancy, "duty", "duties", "responsibilities")
    qualifications = fields(vacancy, "requirement.qualification", "requirement.education", "requirements", "qualification", "requirement")
    experience = fields(vacancy, "requirement.experience", "experience")
    schedule = fields(vacancy, "schedule", "work_schedule", "work-schedule")
    employment = fields(vacancy, "employment", "employment_type", "employment-type")
    prose = unique_fields(description + duties + qualifications)
    findings, candidates = [], []
    summary = {}
    for topic, structured, expected, clues in (
        ("title", title, "job-name", r"(?:должность|ваканси[яию])\s*[:—-]"),
        ("duties", duties, "duty", r"обязанност|предстоит|будете|в задачи"),
        ("requirements", qualifications, "requirement.qualification", r"требован|необходим|ожидаем|кандидат|опыт"),
        ("employment", employment, "employment", r"занятост|ставк"),
    ):
        text_sources = unique_fields(structured + [f for f in description if re.search(clues, f.text)])
        summary[topic] = " / ".join(sorted({f.text for f in text_sources}))
        if topic == "requirements" and any(number(f.value) for f in experience):
            summary[topic] += " / " + " / ".join(f.text for f in experience)
            continue
        if topic == "employment":
            text_sources = unique_fields(text_sources + [f for f in prose if employment_kind(f.text)])
            kinds = [(kind, f) for f in text_sources if (kind := employment_kind(f.text)) and not CONDITIONAL.search(f.text)]
            if len({kind for kind, _ in kinds}) > 1:
                left, right = next(f for kind, f in kinds if kind == "full"), next(f for kind, f in kinds if kind == "part")
                findings.append(issue(topic, "conflict", [left, right], "Указаны и полная, и частичная занятость как обязательные условия."))
                summary[topic] = f"{left.text} / {right.text}"
                continue
            if kinds:
                summary[topic] = kinds[0][1].text
                continue
        if topic == "duties" and not text_sources and description:
            text_sources = description
        if not text_sources:
            findings.append(missing(topic, expected))
        elif topic == "title" and text_sources[0].text in {"специалист", "сотрудник", "менеджер", "помощник", "рабочий"}:
            findings.append(issue(topic, "ambiguous", text_sources[:1], VAGUE[topic]))
        elif topic == "duties" and specific_duties(text_sources):
            continue
        elif topic == "requirements" and specific_requirements(text_sources):
            continue
        elif topic in {"duties", "requirements", "employment"}:
            candidates.extend(examples_for(topic, text_sources))

    explicit_exp = [(int(match[1]), f) for f in prose for match in EXPERIENCE.finditer(f.text)
                    if re.search(r"опыт|стаж", f.text) and not CONDITIONAL.search(f.text)]
    if explicit_exp:
        claimed, source = explicit_exp[0]
        structured_exp = next((f for f in experience if isinstance(f.value, (int, str)) and str(f.value).isdigit()), None)
        if structured_exp and claimed != int(structured_exp.value):
            kind = "ambiguous" if int(structured_exp.value) == 0 else "conflict"
            problem = "В поле опыта стоит 0, а текст устанавливает срок. Нужно проверить значение поля." if kind == "ambiguous" else "В структурированном поле и тексте разные требования к опыту."
            findings.append(issue("requirements", kind, [structured_exp, source], problem, "Какой минимальный стаж обязателен для этой должности?"))

    for topic, review in (
        ("pay", pay_review(vacancy, prose)),
        ("schedule", schedule_review(schedule, prose)),
        ("workplace", workplace_review(addresses(vacancy), prose)),
    ):
        issues, options, value = review
        findings.extend(issues)
        candidates.extend(options)
        summary[topic] = value

    company = fields(vacancy, "company.name", "companyName", "employer.name", "employer")
    employer_id = fields(vacancy, "company.companycode", "company.id", "employer.id", "companycode")
    record = {
        "id": vacancy["id"], "url": link_for(vacancy),
        "title": clean(title[0].value) if title else "Должность не указана",
        "employer": clean(company[0].value) if company else None,
        "employer_id": str(employer_id[0].value) if employer_id else None,
        "issues": findings,
    }
    return {"record": record, "conditions": summary, "diagnostics": diagnostics_for(vacancy)}, candidates


def analyse_group(vacancies, classifier):
    prepared = [prepare(vacancy) for vacancy in vacancies]
    if classifier is not None:
        candidates = [(index, topic, field) for index, (_, items) in enumerate(prepared) for topic, field in items]
        scores = classifier.scores([(topic, field.text) for _, topic, field in candidates])
        best = {}
        for (index, topic, field), score in zip(candidates, scores, strict=True):
            if score > best.get((index, topic), (0, field))[0]:
                best[index, topic] = (score, field)
        for (index, topic), (score, field) in best.items():
            record = prepared[index][0]["record"]
            if score >= classifier.threshold(topic) and not any(item["topic"] == topic for item in record["issues"]):
                record["issues"].append(issue(topic, "ambiguous", [field], VAGUE[topic], method="ml"))
    for result, _ in prepared:
        result["record"]["issues"].sort(key=lambda item: list(TOPICS).index(item["topic"]))
    return [result for result, _ in prepared]
