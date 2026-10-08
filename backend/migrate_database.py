"""Copia idempotente de SQLite a PostgreSQL; los archivos originales se conservan."""
import argparse
import os
from pathlib import Path
import sqlite3
from dotenv import load_dotenv
from app.detections import DetectionStore
from app.alerts import AlertStore
from app.inference_logs import InferenceLogStore
from app.users import UserStore
from app.database import connect

ROOT = Path(__file__).resolve().parent


def migrate(url, detections, logs):
    for store in (DetectionStore(url), AlertStore(url), InferenceLogStore(url), UserStore(url)):
        connection = store.connect()
        with connection:
            pass
        connection.close()
    target = connect(url)
    counts = {}
    try:
        with target:
            for path, tables in ((detections, ("detections", "alerts", "closed_alert_sessions")),
                                 (logs, ("inference_logs",))):
                if not Path(path).is_file():
                    raise FileNotFoundError(path)
                source = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
                try:
                    for table in tables:
                        if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                            continue
                        rows = source.execute(f"SELECT * FROM {table}")
                        columns = [column[0] for column in rows.description]
                        # Nombres exclusivamente del esquema conocido.
                        known = target.execute(f"SELECT * FROM {table} LIMIT 0").description
                        if columns != [column.name for column in known]:
                            raise ValueError(f"Esquema incompatible: {table}")
                        query = f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)}) ON CONFLICT DO NOTHING"
                        counts[table] = sum(target.execute(query, row).rowcount for row in rows)
                finally:
                    source.close()
    finally:
        target.close()
    return counts


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", type=Path, default=ROOT / "data/detections.sqlite3")
    parser.add_argument("--logs", type=Path, default=ROOT / "data/inference_logs.sqlite3")
    args = parser.parse_args()
    print(migrate(os.environ["SENTRIX_DATABASE_URL"], args.detections, args.logs))
