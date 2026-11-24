"use strict";

const $ = (id) => document.getElementById(id);
const kindLabels = {conflict: "Противоречие", missing: "Пропуск", ambiguous: "Уточнить", review: "Проверить повторно", clear: "Без замечаний"};
const statusLabels = {todo: "К отправке", waiting: "Ждём ответа", resolved: "Уточнено", review: "Условие изменилось"};
const fieldLabels = {duty: "Обязанности", "requirement.qualification": "Квалификация", "requirement.experience": "Опыт", "job-name": "Должность", schedule: "Рабочий график", salary_min: "Оплата от", salary_max: "Оплата до", description: "Описание", employment: "Занятость"};
const rank = {conflict: 4, review: 3, missing: 2, ambiguous: 1};
const model = {data: null, selected: null, topic: null};
let noticeTimer;
let draftSelection;
let draftRevision = 0;

function element(tag, text, classes) {
  const part = document.createElement(tag);
  if (text !== undefined && text !== null) part.textContent = text;
  if (classes) part.className = classes;
  return part;
}

function button(text, action, tone = "secondary") {
  const control = element("button", text, tone);
  control.type = "button";
  control.addEventListener("click", action);
  return control;
}

function badge(type, label = kindLabels[type]) {
  return element("span", label, "badge " + type);
}

function notify(message) {
  const status = $("message");
  status.hidden = false;
  status.textContent = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { status.hidden = true; }, 4400);
}

function displayDate(instant) {
  if (!instant) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short", timeStyle: "short", timeZone: "Europe/Kaliningrad"
  }).format(new Date(instant));
}

function stateFor(vacancy, topic) {
  return model.data.states[vacancy.id]?.[topic] || {};
}

function changesFor(vacancy) {
  return Object.entries(model.data.states[vacancy.id] || {}).filter(([, row]) => row.status === "review");
}

function issuesFor(vacancy) {
  return vacancy.issues.filter((issue) => stateFor(vacancy, issue.topic).status !== "resolved");
}

function priorities(vacancy) {
  return [...issuesFor(vacancy).map(({type}) => rank[type]), ...changesFor(vacancy).map(() => rank.review)];
}

function normal(value) {
  return String(value ?? "").trim().toLowerCase().replaceAll("ё", "е");
}

function currentList() {
  const query = normal($("search").value);
  const topic = $("topic-filter").value;
  const kind = $("type-filter").value;
  return model.data.report.vacancies.filter((vacancy) => {
    if (query && ![vacancy.id, vacancy.title, vacancy.employer].some((value) => normal(value).includes(query))) return false;
    if (!topic && !kind) return true;
    if ((!kind || kind === "review") && changesFor(vacancy).some(([key]) => !topic || key === topic)) return true;
    return vacancy.issues.some((issue) => (!topic || topic === issue.topic) && (!kind || kind === issue.type));
  }).sort((left, right) => {
    const l = priorities(left);
    const r = priorities(right);
    return Math.max(0, ...r) - Math.max(0, ...l) || r.length - l.length || left.title.localeCompare(right.title, "ru");
  });
}

function renderQueue() {
  const list = currentList();
  const container = $("queue-list");
  container.replaceChildren();
  $("match-count").textContent = String(list.length);
  if (!list.some(({id}) => id === model.selected)) {
    model.selected = list[0]?.id ?? null;
    model.topic = null;
  }
  if (!list.length) container.append(element("p", "Измените поиск или фильтры.", "empty"));
  for (const vacancy of list) {
    const control = button(undefined, () => {
      model.selected = vacancy.id;
      model.topic = null;
      renderQueue();
      [...container.children].find((row) => row.dataset.id === vacancy.id)?.focus();
    }, "queue-row");
    control.dataset.id = vacancy.id;
    control.setAttribute("aria-current", String(model.selected === vacancy.id));
    control.append(element("span", vacancy.title, "row-title"));
    control.append(element("small", [vacancy.employer, vacancy.id].filter(Boolean).join(" · ")));
    const tags = element("span", undefined, "row-tags");
    const types = new Set(issuesFor(vacancy).map(({type}) => type));
    if (changesFor(vacancy).length) types.add("review");
    for (const type of [...types].sort((a, b) => rank[b] - rank[a])) tags.append(badge(type));
    if (!types.size) tags.append(vacancy.issues.length ? badge("resolved", "Уточнено") : badge("clear"));
    control.append(tags);
    container.append(control);
  }
  renderDetail(list.find(({id}) => id === model.selected));
}

function questionForChange(vacancy, topic) {
  return "Подтвердите актуальное условие по теме «" + model.data.topics[topic] + "» для должности «" + vacancy.title + "».";
}

function questions(vacancy) {
  const items = issuesFor(vacancy).filter(({topic}) => stateFor(vacancy, topic).status !== "waiting");
  const covered = new Set(items.map(({topic}) => topic));
  return [
    ...items.map(({recommendation}) => recommendation),
    ...changesFor(vacancy).filter(([topic]) => !covered.has(topic)).map(([topic]) => questionForChange(vacancy, topic))
  ];
}

function listText(items) {
  return items.map((line, index) => (index + 1) + ". " + line).join("\n");
}

function sameEmployer(left, right) {
  if (left.id === right.id) return true;
  return Boolean(left.employer_id && left.employer_id === right.employer_id);
}

function groupQuestions(vacancy) {
  const rows = model.data.report.vacancies.filter((row) => sameEmployer(row, vacancy));
  const groups = rows.flatMap((row) => {
    const lines = questions(row);
    return lines.length ? [row.title + " · " + row.id + "\n" + listText(lines)] : [];
  });
  $("group-title").textContent = vacancy.employer ? "Вопросы работодателю «" + vacancy.employer + "»" : "Вопросы работодателю";
  $("group-text").value = groups.length ? "Добрый день! Просим уточнить условия по следующим вакансиям.\n\n" + groups.join("\n\n") : "Открытых вопросов к отправке нет.";
  $("group-copy").disabled = !groups.length;
  $("group-dialog").showModal();
}

async function copy(text, visibleField) {
  try {
    await navigator.clipboard.writeText(text);
    notify("Текст скопирован");
  } catch {
    if (visibleField) {
      visibleField.select();
      notify("Выделили текст для копирования");
    } else {
      $("group-title").textContent = "Вопросы по вакансии";
      $("group-text").value = text;
      $("group-copy").disabled = false;
      $("group-dialog").showModal();
      $("group-text").select();
    }
  }
}

function displayEvidence(value) {
  return String(value ?? "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

function draftChoice(label, value, checked) {
  const wrapper = element("label", undefined, "draft-choice");
  const input = element("input");
  input.type = "checkbox";
  input.value = value;
  input.checked = checked;
  wrapper.append(input, element("span", label));
  return wrapper;
}

function resetDraftResult() {
  draftRevision++;
  $("draft-run").disabled = false;
  $("draft-message").textContent = "";
  $("draft-result").hidden = true;
  $("draft-result").replaceChildren();
}

function openDraft(vacancy, issue) {
  draftSelection = {id: vacancy.id, evidence: issue.evidence};
  $("draft-mode").value = "";
  $("draft-days").hidden = true;
  $("draft-week").replaceChildren(...["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day, i) => draftChoice(day, i, false)));
  $("draft-fields").replaceChildren(...issue.evidence.map((item, i) => draftChoice((fieldLabels[item.field] || item.field) + " — " + displayEvidence(item.quote), i, true)));
  resetDraftResult();
  $("draft-dialog").showModal();
  $("draft-mode").focus();
}

function draftChecked(id) {
  return [...$(id).querySelectorAll("input:checked")].map((input) => Number(input.value));
}

async function runDraft() {
  resetDraftResult();
  const revision = draftRevision;
  $("draft-run").disabled = true;
  $("draft-message").textContent = "Проверяем черновик…";
  try {
    const response = await fetch("/preview", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...draftSelection, mode: $("draft-mode").value, days: draftChecked("draft-week"), fields: draftChecked("draft-fields")})
    });
    const result = await response.json();
    if (revision !== draftRevision) return;
    if (!response.ok) throw new Error(result.error);
    $("draft-message").textContent = result.workplace_conflict ? "Противоречие формата работы осталось." : "Противоречие формата работы устранено в черновике.";
    const root = $("draft-result");
    const summary = element("p", "Закрыто замечаний: " + result.closed.length + ". Осталось замечаний: " + result.remaining.length + ".", "draft-intro");
    root.append(summary);
    for (const entry of result.closed) root.append(element("p", "Закрыто: " + model.data.topics[entry.topic] + " — " + entry.problem));
    for (const entry of result.remaining) root.append(element("p", "Осталось: " + model.data.topics[entry.topic] + " — " + entry.problem));
    for (const change of result.changes) {
      root.append(element("h3", fieldLabels[change.field] || change.field));
      const pair = element("div", undefined, "draft-comparison");
      for (const [label, content] of [["До", change.before], ["После", change.after]]) {
        const paragraph = element("p");
        paragraph.append(element("strong", label), document.createTextNode(displayEvidence(content)));
        pair.append(paragraph);
      }
      root.append(pair);
    }
    const text = element("textarea");
    text.readOnly = true;
    text.setAttribute("aria-label", "Черновик для копирования");
    text.value = result.changes.map((change) => (fieldLabels[change.field] || change.field) + "\n" + displayEvidence(change.after)).join("\n\n");
    root.append(text, button("Скопировать черновик", () => copy(text.value, text)));
    root.hidden = false;
  } catch (error) {
    if (revision === draftRevision) $("draft-message").textContent = error.message;
  } finally {
    if (revision === draftRevision) $("draft-run").disabled = false;
  }
}

function exportCard(vacancy) {
  const lines = [vacancy.title, vacancy.id, vacancy.employer || "Работодатель не указан"];
  if (vacancy.url) lines.push(vacancy.url);
  if (!vacancy.issues.length) lines.push("", "Замечаний по проверенным условиям нет.");
  for (const issue of vacancy.issues) {
    const state = stateFor(vacancy, issue.topic);
    lines.push("", model.data.topics[issue.topic] + " — " + (statusLabels[state.status] || "К отправке"), issue.problem);
    for (const item of issue.evidence) lines.push(item.field + ": " + (displayEvidence(item.quote) || "Сведения отсутствуют"));
    lines.push("Вопрос: " + issue.recommendation);
  }
  for (const [topic, state] of changesFor(vacancy)) {
    lines.push("", model.data.topics[topic] + " — проверить повторно", "Было: " + (state.confirmed || "—"), "Стало: " + (state.current || "—"));
  }
  for (const [topic, state] of Object.entries(model.data.states[vacancy.id] || {})) {
    if (state.status && state.status !== "review" && !vacancy.issues.some((issue) => issue.topic === topic)) {
      lines.push("", model.data.topics[topic] + " — " + statusLabels[state.status], state.current || "—");
    }
  }
  const file = new Blob([lines.join("\n") + "\n"], {type: "text/plain; charset=utf-8"});
  const url = URL.createObjectURL(file);
  const link = element("a");
  link.href = url;
  link.download = (vacancy.id.replace(/[^\p{L}\p{N}_.-]/gu, "_") || "vacancy") + ".txt";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function statusSelect(vacancy, topic) {
  const state = stateFor(vacancy, topic);
  if (!state.version) return null;
  const control = element("select");
  control.setAttribute("aria-label", "Статус: " + model.data.topics[topic]);
  control.dataset.topicStatus = topic;
  for (const value of ["todo", "waiting", "resolved"]) {
    const option = element("option", statusLabels[value]);
    option.value = value;
    control.append(option);
  }
  if (state.status === "review") {
    const option = element("option", statusLabels.review);
    option.value = "review";
    option.disabled = true;
    control.prepend(option);
  }
  control.value = state.status || "todo";
  control.addEventListener("change", async () => {
    control.disabled = true;
    try {
      const response = await fetch("/status", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: vacancy.id, topic, status: control.value, version: state.version})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      await load(true);
      document.querySelector('[data-topic-status="' + topic + '"]')?.focus();
      notify("Статус сохранён");
    } catch (error) {
      control.value = state.status || "todo";
      control.disabled = false;
      notify(error.message);
    }
  });
  return control;
}

function issueCard(vacancy, issue) {
  const article = element("article", undefined, "issue " + issue.type);
  const header = element("div", undefined, "issue-head");
  header.append(element("h3", model.data.topics[issue.topic]), badge(issue.type));
  article.append(header, element("p", issue.problem));
  const evidence = element("div", undefined, "evidence");
  for (const item of issue.evidence) {
    const quote = element("blockquote");
    const source = element("code", fieldLabels[item.field] ? fieldLabels[item.field] + " (" + item.field + ")" : item.field);
    quote.append(source, element("span", displayEvidence(item.quote) || "Сведения отсутствуют"));
    evidence.append(quote);
  }
  const question = element("p", undefined, "question");
  question.append(element("strong", "Вопрос работодателю"), document.createTextNode(issue.recommendation));
  const actions = element("div", undefined, "issue-actions");
  actions.append(button("Скопировать вопрос", () => copy(issue.recommendation)));
  if (issue.topic === "workplace" && issue.type === "conflict") {
    actions.append(button("Проверить перед публикацией", () => openDraft(vacancy, issue)));
  }
  const status = statusSelect(vacancy, issue.topic);
  if (status) actions.append(status);
  article.append(evidence, question, actions);
  return article;
}

function changeCard(vacancy, topic, state) {
  const article = element("article", undefined, "issue review");
  const heading = element("div", undefined, "issue-head");
  heading.append(element("h3", model.data.topics[topic]), badge("review"));
  article.append(heading, element("p", "Было: " + (state.confirmed || "—")), element("p", "Стало: " + (state.current || "—")));
  if (!vacancy.issues.some((issue) => issue.topic === topic)) {
    const actions = element("div", undefined, "issue-actions");
    actions.append(button("Скопировать вопрос", () => copy(questionForChange(vacancy, topic))));
    const status = statusSelect(vacancy, topic);
    if (status) actions.append(status);
    article.append(actions);
  }
  return article;
}

function renderDetail(vacancy) {
  const root = $("detail");
  root.replaceChildren();
  if (!vacancy) {
    root.append(element("p", "Вакансии по текущим фильтрам не найдены.", "empty"));
    return;
  }
  const identity = element("p", [vacancy.employer, vacancy.id].filter(Boolean).join(" · "), "identity-line");
  if (/^https?:\/\//i.test(vacancy.url || "")) {
    const link = element("a", "Карточка на портале");
    link.href = vacancy.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    identity.append(link);
  }
  root.append(element("h2", vacancy.title), identity);
  const actions = element("div", undefined, "actions");
  const available = questions(vacancy);
  const copyAll = button("Скопировать вопросы", () => copy(listText(available)), "primary");
  copyAll.disabled = !available.length;
  actions.append(copyAll, button("Пакет работодателю", () => groupQuestions(vacancy)), button("Выгрузить карточку", () => exportCard(vacancy)));
  root.append(actions);
  const topics = element("div", undefined, "topic-map");
  for (const [topic, label] of Object.entries(model.data.topics)) {
    const kind = stateFor(vacancy, topic).status === "review" ? "review" : (vacancy.issues.find((issue) => issue.topic === topic)?.type || "clear");
    const control = button(label, () => {
      model.topic = model.topic === topic ? null : topic;
      renderDetail(vacancy);
      root.querySelector('[data-topic="' + topic + '"]')?.focus();
    }, "topic");
    control.dataset.topic = topic;
    control.setAttribute("aria-pressed", String(model.topic === topic));
    control.append(element("span", kindLabels[kind]));
    topics.append(control);
  }
  root.append(topics);
  const alerts = changesFor(vacancy).filter(([topic]) => !model.topic || model.topic === topic);
  const issues = vacancy.issues.filter(({topic}) => !model.topic || model.topic === topic);
  if (alerts.length || issues.length) {
    root.append(element("h3", "Уточнения и замечания", "review-heading"));
    for (const [topic, state] of alerts) root.append(changeCard(vacancy, topic, state));
    for (const issue of issues) root.append(issueCard(vacancy, issue));
  } else if (model.topic) {
    const state = stateFor(vacancy, model.topic);
    const item = element("article", undefined, "issue");
    item.append(element("h3", model.data.topics[model.topic]), element("p", state.current || "Замечаний по этой теме нет."));
    const status = statusSelect(vacancy, model.topic);
    if (status) {
      const controls = element("div", undefined, "issue-actions");
      controls.append(status);
      item.append(controls);
    }
    root.append(item);
  } else {
    root.append(element("p", "Замечаний по проверенным условиям нет.", "empty"));
  }
}

function renderSummary(report) {
  const stats = $("metrics");
  const count = report.vacancies.filter((vacancy) => priorities(vacancy).length).length;
  const conflicts = report.vacancies.reduce((sum, vacancy) => sum + issuesFor(vacancy).filter(({type}) => type === "conflict").length, 0);
  stats.replaceChildren();
  for (const [value, label] of [[report.vacancies.length, "Всего вакансий"], [count, "Требуют внимания"], [conflicts, "Явные противоречия"]]) {
    const card = element("div", undefined, "metric");
    card.append(element("strong", value), element("span", label));
    stats.append(card);
  }
  const meta = report.meta;
  const destination = $("passport-fields");
  destination.replaceChildren();
  for (const [name, value] of [
    ["Источник", meta.source], ["Режим", meta.mode === "api" ? "API региона 39" : "Проверочный файл"],
    ["Проверено", displayDate(meta.generated_at)], ["Страниц", meta.pages],
    ["Версия модели", meta.model], ["Версия правил", meta.rules],
    ["Пересчитано вакансий", meta.analysed], ["Взято из кэша", meta.reused]
  ]) destination.append(element("dt", name), element("dd", value ?? "—"));
  $("diagnostics").replaceChildren(...(meta.diagnostics || []).map((note) => element("li", note)));
}

async function load(quiet = false) {
  $("refresh").disabled = true;
  try {
    const response = await fetch("/data");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    model.data = data;
    const report = data.report;
    $("run-info").textContent = report ? "Последний успешный отчёт " + displayDate(report.meta.generated_at) + " по Калининграду" : "Отчёт ещё не сформирован";
    $("run-error").textContent = data.attempt?.error ? "Последняя проверка завершилась ошибкой. " + data.attempt.error : "";
    $("run-error").hidden = !data.attempt?.error;
    $("empty-report").hidden = Boolean(report);
    for (const id of ["metrics", "workspace", "passport"]) $(id).hidden = !report;
    if (report) {
      const filter = $("topic-filter");
      if (filter.options.length === 1) {
        for (const [value, label] of Object.entries(data.topics)) {
          const option = element("option", label);
          option.value = value;
          filter.append(option);
        }
      }
      renderSummary(report);
      renderQueue();
    }
  } catch (error) {
    if (quiet) throw error;
    $("run-error").hidden = false;
    $("run-error").textContent = "Не удалось открыть отчёт. " + error.message;
  } finally {
    $("refresh").disabled = false;
  }
}

$("search").addEventListener("input", renderQueue);
for (const id of ["topic-filter", "type-filter"]) $(id).addEventListener("change", renderQueue);
$("refresh").addEventListener("click", () => load());
$("group-close").addEventListener("click", () => $("group-dialog").close());
$("group-copy").addEventListener("click", () => copy($("group-text").value, $("group-text")));
$("draft-close").addEventListener("click", () => $("draft-dialog").close());
$("draft-mode").addEventListener("change", () => { $("draft-days").hidden = $("draft-mode").value !== "hybrid"; });
$("draft-dialog").addEventListener("change", resetDraftResult);
$("draft-dialog").addEventListener("close", resetDraftResult);
$("draft-run").addEventListener("click", runDraft);
load();
