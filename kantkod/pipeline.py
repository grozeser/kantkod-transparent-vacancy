import json
import sqlite3
from pathlib import Path

from . import analysis
from .analysis import analyse_group
from .model import ClarityModel, version_id
from .storage import cached_results, database, fingerprint, now, publish_report, report_lock, save_attempt, save_run


def run_report(load, output: Path, db_path: Path, mode: str, source: str):
    output = output.resolve()
    if output == db_path.resolve():
        raise ValueError("Отчёт и база состояния должны храниться в разных файлах")
    channel = str(output)
    with report_lock(output):
        try:
            return generate(load, output, db_path, mode, source, channel)
        except (ValueError, OSError, sqlite3.Error) as exc:
            with database(db_path) as connection:
                save_attempt(connection, channel, now(), str(exc))
            raise


def generate(load, output, db_path, mode, source, channel):
    batch = load()
    model = version_id()
    rules = analysis.RULES_VERSION + "-" + fingerprint(Path(analysis.__file__).read_text(encoding="utf-8"))[:12]

    with database(db_path) as connection:
        cache = cached_results(connection, channel)
        pending, digests, results = [], {}, {}
        for vacancy in batch.vacancies:
            identity = vacancy["id"]
            digest = fingerprint([model, rules, vacancy])
            digests[identity] = digest
            if identity in cache and cache[identity][0] == digest:
                results[identity] = digest, json.loads(cache[identity][1])
            else:
                pending.append(vacancy)

        if pending:
            for result in analyse_group(pending, ClarityModel()):
                identity = result["record"]["id"]
                results[identity] = digests[identity], result

        ordered = [results[item["id"]][1] for item in batch.vacancies]
        timestamp = now()
        report = {
            "meta": {
                "schema_version": 1, "mode": mode, "source": source, "generated_at": timestamp,
                "model": model, "rules": rules, "pages": batch.pages, "vacancies": len(ordered),
                "analysed": len(pending), "reused": len(ordered) - len(pending),
                "diagnostics": batch.diagnostics + [notice for item in ordered for notice in item["diagnostics"]],
            },
            "vacancies": [item["record"] for item in ordered],
        }
        save_run(connection, channel, results, timestamp)
        publish_report(output, report)
    return report
