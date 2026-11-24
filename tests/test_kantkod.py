import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from kantkod.analysis import analyse_group
from kantkod.evaluation import BASE, evaluate_model
from kantkod.pipeline import run_report
from kantkod.preview import analyse_draft, preview_vacancy
from kantkod.source import Batch, InputError, load_api, load_file, parse_response, read_json
from kantkod.storage import database, read_editor, report_lock, set_status


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-vacancies.json"


def vacancy(identity="A", **fields):
    return {**BASE, "id": identity, **fields}


def envelope(rows, total):
    return {"status": "200", "meta": {"total": str(total)}, "results": {"vacancies": [{"vacancy": row} for row in rows]}}


def only_issues(row):
    return analyse_group([row], None)[0]["record"]["issues"]


@contextmanager
def fake_api(replies):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            requests.append((int(query["offset"][0]), int(query["limit"][0])))
            response = replies.get(requests[-1][0], 500)
            if isinstance(response, int):
                self.send_error(response)
                return
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/vacancies", requests
        finally:
            server.shutdown()
            worker.join()


class AnalysisTests(unittest.TestCase):
    def test_malformed_rows_report_path(self):
        with self.assertRaisesRegex(InputError, r"results\.vacancies\[0\]\.vacancy\.id"):
            parse_response(envelope([{"job-name": "Инженер"}], 1), "source")

    def test_optional_field_has_diagnostic(self):
        result = analyse_group([vacancy(schedule={})], None)[0]
        self.assertIn("vacancy.schedule", result["diagnostics"][0])
        self.assertEqual(result["record"]["issues"][0]["evidence"], [{"field": "schedule", "quote": ""}])

    def test_contradictions_quote_both_sources(self):
        source = vacancy(duty="Дистанционная работа.", description="Обязательное присутствие в офисе ежедневно.")
        workplace = next(item for item in only_issues(source) if item["topic"] == "workplace")
        self.assertEqual(workplace["type"], "conflict")
        self.assertEqual({entry["quote"] for entry in workplace["evidence"]}, {source["duty"], source["description"]})

    def test_schedule_in_text_covers_structured_label(self):
        source = vacancy(schedule="Сменный график", description="Рабочий график — 2/2, с 08:00 до 20:00.")
        self.assertFalse(any(item["topic"] == "schedule" for item in only_issues(source)))

    def test_office_address_alone_does_not_contradict_remote(self):
        self.assertFalse(any(item["topic"] == "workplace" for item in only_issues(vacancy(description="Полностью дистанционная работа."))))

    def test_review_cases_preserve_the_meaning_and_source_of_each_field(self):
        cases = [
            ("workplace", "conflict", {"description": "Работа полностью дистанционная. Обязательно ежедневное присутствие в офисе."}),
            ("workplace", "conflict", {"description": "Только удалённый формат, но ежедневно нужно присутствовать в офисе."}),
            ("workplace", None, {"description": "Полностью дистанционная работа. Присутствие в офисе не требуется."}),
            ("workplace", None, {"description": "Работа полностью дистанционная. Ежедневное присутствие в офисе необязательно."}),
            ("workplace", "conflict", {"description": "<p>Полностью удалённая работа.</p><p>Обязательное присутствие в офисе каждое утро.</p>"}),
            ("workplace", None, {"description": "Гибридный формат: два дня в офисе, три дня дома."}),
            ("duties", "missing", {"duty": None, "description": "Оплата 47 000 рублей в месяц."}),
            ("duties", "missing", {"duty": None, "description": "Оклад 47000 ₽"}),
            ("duties", None, {"duty": None, "description": "Вести реестр обращений и готовить акты осмотра."}),
            ("workplace", None, {"addresses": None, "description": "Место работы: Калининград, ул. Мира, д. 8."}),
            ("workplace", None, {"addresses": None, "description": "Место работы: Калининград, ул. Мира, д. 18."}),
            ("schedule", "conflict", {"schedule": "График 5/2, 09:00–18:00", "description": "График работы: два дня через два, 09:00–18:00."}),
            ("schedule", "conflict", {"schedule": "График 2/2", "description": "Рабочий график с понедельника по пятницу."}),
            ("schedule", None, {"schedule": "Сменный график 2/2", "description": "График работы: два дня через два."}),
            ("schedule", "conflict", {"schedule": "5/2", "description": "Рабочий график: сутки через трое."}),
            ("schedule", None, {"schedule": "Сменный график 1/3", "description": "Рабочий график: сутки через трое."}),
            ("schedule", None, {"schedule": "Гибкий график", "description": "Рабочий график: по будням, 10:00–18:00."}),
            ("pay", None, {"description": "Оклад 12 000 руб. за неделю."}),
            ("pay", None, {"description": "Оклад 12 000 ₽/неделю."}),
            ("pay", "conflict", {"description": "Оклад 120 000 руб. в месяц."}),
            ("title", "conflict", {"job-name": "Водитель автобуса", "description": "Должность: бухгалтер. Готовить квартальные отчёты."}),
            ("title", None, {"job-name": "Инженер по эксплуатации зданий", "description": "Должность: инженер по эксплуатации."}),
        ]
        from kantkod.model import ClarityModel

        rows = [vacancy(f"review-{index}", **change) for index, (_, _, change) in enumerate(cases)]
        findings = analyse_group(rows, ClarityModel())
        for row, result, (topic, kind, _) in zip(rows, findings, cases, strict=True):
            with self.subTest(vacancy=row["id"], topic=topic, kind=kind):
                notes = [item for item in result["record"]["issues"] if item["topic"] == topic]
                self.assertEqual([item["type"] for item in notes], [kind] if kind else [])
                if topic == "workplace" and notes:
                    evidence = notes[0]["evidence"]
                    self.assertEqual(len(evidence), 2)
                    self.assertNotEqual(evidence[0]["quote"], evidence[1]["quote"])
                    self.assertTrue(all(item["quote"] in row[item["field"]] for item in evidence))

    def test_actual_and_legal_addresses_can_share_a_field(self):
        row = vacancy(addresses=None, description="Юридический адрес: ул. Морская, д. 3. Место работы: Гусев, ул. Заводская, д. 9.")
        result = analyse_group([row], None)[0]
        self.assertEqual(result["conditions"]["workplace"], "гусев, ул. заводская, д. 9")
        self.assertFalse(any(item["topic"] == "workplace" for item in result["record"]["issues"]))

    def test_hybrid_is_compatible_with_office_on_one_day(self):
        row = vacancy(description="Гибридный формат. Обязательное присутствие в офисе по средам.")
        self.assertFalse(any(item["topic"] == "workplace" for item in only_issues(row)))

    def test_draft_resolves_conflict_only_when_both_fields_are_consistent(self):
        row = next(item for item in load_file(SAMPLE).vacancies if item["id"] == "DEMO-39-004")
        original = json.dumps(row, sort_keys=True)
        before = only_issues(row)
        for selection in ([0], [1]):
            trial = analyse_draft(row, before, selection, "hybrid", [2])
            self.assertTrue(trial["workplace_conflict"])
            self.assertEqual(len(trial["changes"]), 1)
        accepted = analyse_draft(row, before, [0, 1], "hybrid", [2])
        self.assertFalse(accepted["workplace_conflict"])
        self.assertEqual(len(accepted["changes"]), 2)
        self.assertEqual([item["topic"] for item in accepted["closed"]], ["workplace"])
        self.assertTrue(all("среда" in item["after"] for item in accepted["changes"]))
        self.assertIn("Приём звонков", next(item["after"] for item in accepted["changes"] if item["field"] == "duty"))
        self.assertEqual(json.dumps(row, sort_keys=True), original)
        with self.assertRaisesRegex(ValueError, "дни присутствия"):
            analyse_draft(row, before, [0, 1], "hybrid", [])

    def test_local_sample_counts_and_evidence(self):
        with TemporaryDirectory() as directory:
            output, store = Path(directory) / "output.json", Path(directory) / "state.sqlite3"
            report = run_report(lambda: load_file(SAMPLE), output, store, "file", SAMPLE.name)
            batch = load_file(SAMPLE)
            self.assertEqual(report, read_json(output))
            self.assertEqual({row["id"] for row in report["vacancies"]}, {row["id"] for row in batch.vacancies})
            self.assertEqual(len(report["vacancies"]), 10)
            rows = {row["id"]: row for row in report["vacancies"]}
            self.assertEqual(rows["DEMO-39-007"]["issues"], [])
            self.assertTrue(any(item["method"] == "ml" for item in rows["DEMO-39-002"]["issues"]))
            self.assertTrue(any(item["topic"] == "schedule" and item["type"] == "ambiguous" for item in rows["DEMO-39-008"]["issues"]))
            self.assertEqual(len(next(item for item in rows["DEMO-39-004"]["issues"] if item["type"] == "conflict")["evidence"]), 2)
            self.assertTrue(all(row["url"] is None for row in rows.values()))

    def test_model_adds_confirmed_findings_on_held_out_cases(self):
        comparison = evaluate_model("holdout")
        self.assertGreater(comparison["rules_with_ml"]["true_positives"], comparison["rules"]["true_positives"])
        self.assertLessEqual(comparison["rules_with_ml"]["false_positives"], comparison["rules"]["false_positives"])


class ApiTests(unittest.TestCase):
    def test_collects_two_pages_respects_limit_and_deduplicates(self):
        rows = {0: envelope([vacancy("A"), vacancy("B")], 6), 1: envelope([vacancy("B"), vacancy("C")], 6)}
        with fake_api(rows) as (url, requests):
            result = load_api(url, 3, 2, "page", 2)
        self.assertEqual(requests, [(0, 2), (1, 2)])
        self.assertEqual([row["id"] for row in result.vacancies], ["A", "B", "C"])
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.diagnostics, ["Повторных записей по ID: 1"])

    def test_supports_item_based_offset(self):
        rows = {0: envelope([vacancy("A"), vacancy("B")], 3), 2: envelope([vacancy("C")], 3)}
        with fake_api(rows) as (url, requests):
            result = load_api(url, 4, 2, "item", 2)
        self.assertEqual(requests, [(0, 2), (2, 2)])
        self.assertEqual(len(result.vacancies), 3)

    def test_host_returns_same_page(self):
        row = envelope([vacancy("A")], 2)
        with fake_api({0: row, 1: row}) as (url, _):
            with self.assertRaisesRegex(InputError, "повторил страницу"):
                load_api(url, 3, 1, "page", 2)

    def test_second_page_failure_preserves_success_and_records_error(self):
        with TemporaryDirectory() as directory:
            output, store = Path(directory) / "live.json", Path(directory) / "state.sqlite3"
            with fake_api({0: envelope([vacancy("A")], 2), 1: envelope([vacancy("B")], 2)}) as (url, _):
                run_report(lambda: load_api(url, 3, 1, "page", 2), output, store, "api", url)
            previous_bytes = output.read_bytes()
            with fake_api({0: envelope([vacancy("C")], 2), 1: 503}) as (url, _):
                with self.assertRaisesRegex(InputError, "Страница 2"):
                    run_report(lambda: load_api(url, 3, 1, "page", 2), output, store, "api", url)
            self.assertEqual(output.read_bytes(), previous_bytes)
            with database(store) as connection:
                cards, attempt = read_editor(connection, str(output.resolve()))
            self.assertEqual(set(cards), {"A", "B"})
            self.assertIn("Страница 2", attempt["error"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "check.json"
        self.store = Path(self.directory.name) / "editor.sqlite3"
        self.channel = str(self.output.resolve())

    def run_rows(self, *rows):
        return run_report(lambda: Batch(list(rows), 1, []), self.output, self.store, "file", "fixture.json")

    def states(self):
        with database(self.store) as connection:
            states, _ = read_editor(connection, self.channel)
        return states

    def confirm(self, identity, topic):
        current = self.states()[identity][topic]
        with database(self.store) as connection:
            set_status(connection, self.channel, identity, topic, "resolved", current["version"])

    def test_cache_only_reuses_matching_inputs_and_model_version(self):
        original = vacancy("A")
        unchanged = vacancy("B")
        self.assertEqual(self.run_rows(original, unchanged)["meta"]["analysed"], 2)
        self.assertEqual(self.run_rows(original, unchanged)["meta"]["reused"], 2)
        changed = {**original, "schedule": "2/2, 09:00–21:00"}
        self.assertEqual(self.run_rows(changed, unchanged)["meta"]["analysed"], 1)
        with patch("kantkod.pipeline.version_id", return_value="next-model"):
            self.assertEqual(self.run_rows(changed, unchanged)["meta"]["analysed"], 2)

    def test_reopens_only_changed_confirmation_without_adding_issue(self):
        original = vacancy(schedule="2/2, 08:00–20:00")
        self.run_rows(original)
        self.confirm("A", "schedule")
        self.confirm("A", "pay")
        self.run_rows(vacancy(schedule="2 / 2, 08:00 до 20:00"))
        self.assertEqual(self.states()["A"]["schedule"]["status"], "resolved")

        fresh = vacancy(schedule="5/2, 09:00–18:00")
        report = self.run_rows(fresh)
        state = self.states()["A"]
        self.assertEqual(state["schedule"]["status"], "review")
        self.assertIn("2/2", state["schedule"]["confirmed"])
        self.assertIn("5/2", state["schedule"]["current"])
        self.assertEqual(state["pay"]["status"], "resolved")
        self.assertFalse(any(issue["topic"] == "schedule" for issue in report["vacancies"][0]["issues"]))
        self.run_rows(fresh)
        self.assertEqual(self.states()["A"]["schedule"], state["schedule"])
        self.confirm("A", "schedule")
        self.run_rows(fresh)
        self.assertEqual(self.states()["A"]["schedule"]["status"], "resolved")

    def test_changed_condition_can_also_have_actual_conflict(self):
        self.run_rows(vacancy(schedule="2/2, 08:00–20:00"))
        self.confirm("A", "schedule")
        fresh = vacancy(schedule="5/2, 09:00–18:00", description="Рабочий график 3/3, 09:00–18:00.")
        report = self.run_rows(fresh)
        self.assertEqual(self.states()["A"]["schedule"]["status"], "review")
        self.assertEqual(len(next(issue for issue in report["vacancies"][0]["issues"] if issue["topic"] == "schedule")["evidence"]), 2)

    def test_changed_address_in_prose_reopens_only_workplace(self):
        self.run_rows(vacancy(addresses=None, description="Место работы: Калининград, ул. Мира, д. 8."))
        self.confirm("A", "workplace")
        self.confirm("A", "pay")
        report = self.run_rows(vacancy(addresses=None, description="Место работы: Калининград, ул. Мира, д. 18."))
        state = self.states()["A"]
        self.assertEqual(state["workplace"]["status"], "review")
        self.assertIn("18", state["workplace"]["current"])
        self.assertEqual(state["pay"]["status"], "resolved")
        self.assertFalse(any(issue["topic"] == "workplace" for issue in report["vacancies"][0]["issues"]))

    def test_stale_browser_state_cannot_confirm_new_value(self):
        self.run_rows(vacancy())
        stale = self.states()["A"]["schedule"]["version"]
        self.run_rows(vacancy(schedule="2/2, 09:00–21:00"))
        with database(self.store) as connection:
            with self.assertRaisesRegex(ValueError, "Данные изменились"):
                set_status(connection, self.channel, "A", "schedule", "resolved", stale)

    def test_report_lock_conflicts_on_same_output(self):
        with report_lock(self.output):
            with self.assertRaisesRegex(ValueError, "уже обновляется"):
                with report_lock(self.output):
                    self.fail("Second writer acquired the same lock")

    def test_draft_rejects_stale_evidence_and_keeps_the_saved_report(self):
        original = vacancy(duty="Работа полностью дистанционная.", description="Обязательное ежедневное присутствие в офисе.")
        report = self.run_rows(original)
        finding = next(item for item in report["vacancies"][0]["issues"] if item["topic"] == "workplace")
        data = {"id": "A", "mode": "hybrid", "days": [2], "fields": [0, 1], "evidence": finding["evidence"]}
        before = self.output.read_bytes()
        with database(self.store) as connection:
            self.assertFalse(preview_vacancy(connection, self.channel, data)["workplace_conflict"])
            with self.assertRaisesRegex(ValueError, "Исходные условия изменились"):
                preview_vacancy(connection, self.channel, {**data, "evidence": []})
        self.assertEqual(self.output.read_bytes(), before)
        self.assertNotIn("draft_source", before.decode())


if __name__ == "__main__":
    unittest.main()
