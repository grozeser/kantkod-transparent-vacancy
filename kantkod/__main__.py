import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .pipeline import run_report
from .source import InputError, load_api, load_file, read_json


ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m kantkod", description="Прозрачная вакансия")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Проверить локальный JSON")
    check.add_argument("--input", type=Path, required=True)
    check.add_argument("--output", type=Path, default=Path("var/check-report.json"))
    check.add_argument("--db", type=Path, default=Path("var/editor.sqlite3"))

    live = commands.add_parser("live", help="Получить и проверить вакансии региона 39")
    live.add_argument("--config", type=Path, default=ROOT / "config.json")
    live.add_argument("--url")
    live.add_argument("--limit", type=int)
    live.add_argument("--page-size", type=int)
    live.add_argument("--offset-unit", choices=("page", "item"))
    live.add_argument("--output", type=Path, default=Path("var/live-report.json"))
    live.add_argument("--db", type=Path, default=Path("var/editor.sqlite3"))

    serve = commands.add_parser("serve", help="Открыть локальное рабочее место редактора")
    serve.add_argument("--report", type=Path, default=Path("var/check-report.json"))
    serve.add_argument("--db", type=Path, default=Path("var/editor.sqlite3"))
    serve.add_argument("--port", type=int, default=8765)

    evaluate = commands.add_parser("evaluate", help="Сравнить правила и правила с ИИ")
    evaluate.add_argument("--set", choices=("tuning", "holdout"), default="holdout")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .editor import serve_editor

            serve_editor(args.report, args.db, args.port)
            return 0
        if args.command == "evaluate":
            from .evaluation import evaluate_model

            print(json.dumps(evaluate_model(args.set), ensure_ascii=False, indent=2))
            return 0
        if args.command == "check":
            if args.input.resolve() in {args.output.resolve(), args.db.resolve()}:
                raise InputError("Выберите отдельные пути для входного файла, отчёта и базы")
            report = run_report(lambda: load_file(args.input), args.output, args.db, "file", args.input.name)
        else:
            config = read_json(args.config)
            if not isinstance(config, dict):
                raise InputError("Файл конфигурации должен содержать объект")
            url = args.url if args.url is not None else config.get("source_url")
            if not isinstance(url, str):
                raise InputError("Укажите source_url в конфигурации")
            maximum = args.limit if args.limit is not None else int(config["max_vacancies"])
            page_size = args.page_size if args.page_size is not None else int(config["page_size"])
            offset = args.offset_unit if args.offset_unit is not None else config["offset_unit"]
            timeout = float(config["timeout_seconds"])
            url_parts = urlsplit(url)
            source = urlunsplit(url_parts._replace(netloc=url_parts.netloc.rsplit("@", 1)[-1], query="", fragment=""))
            report = run_report(lambda: load_api(url, maximum, page_size, offset, timeout), args.output, args.db, "api", source)
        meta = report["meta"]
        print(f"{args.output} · вакансий {meta['vacancies']} · проверено {meta['analysed']} · из кэша {meta['reused']} · страниц {meta['pages']}")
        for message in meta["diagnostics"]:
            print(message, file=sys.stderr)
        return 0
    except ModuleNotFoundError as exc:
        print(f"Не найдена зависимость {exc}. Установите зависимости из requirements.txt.", file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
