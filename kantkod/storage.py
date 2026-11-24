import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


STATUSES = {"todo", "waiting", "resolved"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def publish_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(report, temp, ensure_ascii=False, indent=2, allow_nan=False)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        json.loads(temp_path.read_text(encoding="utf-8"))
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@contextmanager
def report_lock(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(output.suffix + ".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            take = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            take = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        try:
            take()
        except OSError as exc:
            raise ValueError("Отчёт уже обновляется другим запуском") from exc
        try:
            yield
        finally:
            lock.seek(0)
            release()


@contextmanager
def database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS cache (
                channel TEXT, vacancy_id TEXT, digest TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY (channel, vacancy_id)
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                channel TEXT, vacancy_id TEXT, topic TEXT, value TEXT NOT NULL,
                PRIMARY KEY (channel, vacancy_id, topic)
            );
            CREATE TABLE IF NOT EXISTS states (
                channel TEXT, vacancy_id TEXT, topic TEXT, status TEXT NOT NULL,
                confirmed TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (channel, vacancy_id, topic)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                channel TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, error TEXT
            );
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def cached_results(connection, channel):
    return {
        row["vacancy_id"]: (row["digest"], row["result"])
        for row in connection.execute("SELECT vacancy_id, digest, result FROM cache WHERE channel = ?", (channel,))
    }


def save_run(connection, channel, results, timestamp):
    connection.execute("DELETE FROM snapshots WHERE channel = ?", (channel,))
    current_ids = set(results)
    stale_ids = set(cached_results(connection, channel)) - current_ids
    connection.executemany("DELETE FROM cache WHERE channel = ? AND vacancy_id = ?", ((channel, item) for item in stale_ids))
    for vacancy_id, (digest, payload) in results.items():
        connection.execute("""
            INSERT INTO cache VALUES (?, ?, ?, ?)
            ON CONFLICT(channel, vacancy_id) DO UPDATE SET digest=excluded.digest, result=excluded.result
            WHERE cache.digest <> excluded.digest
        """, (channel, vacancy_id, digest, json.dumps(payload, ensure_ascii=False)))
        for topic, current in payload["conditions"].items():
            connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (channel, vacancy_id, topic, current))
            connection.execute("""
                UPDATE states SET status='review', updated_at=?
                WHERE channel=? AND vacancy_id=? AND topic=? AND status='resolved' AND confirmed <> ?
            """, (timestamp, channel, vacancy_id, topic, current))
    save_attempt(connection, channel, timestamp, None)


def save_attempt(connection, channel, timestamp, error):
    connection.execute("""
        INSERT INTO attempts VALUES (?, ?, ?)
        ON CONFLICT(channel) DO UPDATE SET attempted_at=excluded.attempted_at, error=excluded.error
    """, (channel, timestamp, error))


def read_editor(connection, channel):
    rows = connection.execute("""
        SELECT p.vacancy_id, p.topic, p.value, s.status, s.confirmed, s.updated_at
        FROM snapshots p LEFT JOIN states s
        ON s.channel=p.channel AND s.vacancy_id=p.vacancy_id AND s.topic=p.topic
        WHERE p.channel=?
    """, (channel,))
    cards = {}
    for row in rows:
        cards.setdefault(row["vacancy_id"], {})[row["topic"]] = {
            "status": row["status"], "current": row["value"], "confirmed": row["confirmed"],
            "version": fingerprint(row["value"]), "updated_at": row["updated_at"],
        }
    attempt = connection.execute("SELECT attempted_at, error FROM attempts WHERE channel=?", (channel,)).fetchone()
    return cards, dict(attempt) if attempt else None


def set_status(connection, channel, vacancy_id, topic, status, expected_version):
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("Выберите допустимый статус")
    for value in (vacancy_id, topic, expected_version):
        if not isinstance(value, str):
            raise ValueError("Некорректный запрос статуса")
    connection.execute("BEGIN IMMEDIATE")
    current = connection.execute("SELECT value FROM snapshots WHERE channel=? AND vacancy_id=? AND topic=?", (channel, vacancy_id, topic)).fetchone()
    if current is None or fingerprint(current["value"]) != expected_version:
        raise ValueError("Данные изменились. Обновите отчёт и повторите действие")
    confirmed = current["value"] if status == "resolved" else None
    connection.execute("""
        INSERT INTO states VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel, vacancy_id, topic) DO UPDATE
        SET status=excluded.status, confirmed=excluded.confirmed, updated_at=excluded.updated_at
    """, (channel, vacancy_id, topic, status, confirmed, now()))
