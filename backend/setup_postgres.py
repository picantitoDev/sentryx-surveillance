"""Inicializa PostgreSQL local aislado en puerto 55432 y configura backend/.env."""
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parent
BIN = Path(os.environ.get("SENTRIX_POSTGRES_BIN", "C:/Program Files/PostgreSQL/18/bin"))
DATA = ROOT / "data" / "postgres"
ENV = ROOT / ".env"


def run(tool, *args):
    subprocess.run([str(BIN / (tool + ".exe")), *map(str, args)], check=True)


if __name__ == "__main__":
    if DATA.exists():
        raise SystemExit("El cluster ya existe. Arrancar con pg_ctl; consultar docs/DATABASE.md")
    existing = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    if any(line.startswith("SENTRIX_DATABASE_URL=") and line.split("=", 1)[1].strip()
           for line in existing.splitlines()):
        raise SystemExit("Ya hay conexión configurada; no se modifica")
    password = secrets.token_urlsafe(32)
    DATA.parent.mkdir(parents=True, exist_ok=True)
    password_file = DATA.parent / ".pg-init-password"
    try:
        password_file.write_text(password, encoding="utf-8")
        run("initdb", "-D", DATA, "-U", "sentrix", "--auth=scram-sha-256",
            "--pwfile", password_file, "--encoding=UTF8", "--locale=C")
    finally:
        password_file.unlink(missing_ok=True)
    with (DATA / "postgresql.conf").open("a", encoding="utf-8") as output:
        output.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
    admin_url = f"postgresql://sentrix:{password}@127.0.0.1:55432/postgres"
    (DATA.parent / "postgres-admin.env").write_text(f"SENTRIX_ADMIN_DATABASE_URL={admin_url}\n", encoding="utf-8")
    run("pg_ctl", "-D", DATA, "-l", DATA.parent / "postgres.log", "-w", "start")
    import psycopg
    admin_url = f"postgresql://sentrix:{password}@127.0.0.1:55432/postgres"
    app_password = secrets.token_urlsafe(32)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        from psycopg import sql
        connection.execute(sql.SQL("CREATE ROLE sentrix_app LOGIN PASSWORD {}").format(sql.Literal(app_password)))
        connection.execute("CREATE DATABASE sentrix OWNER sentrix_app")
    lines = [line for line in existing.splitlines() if not line.startswith("SENTRIX_DATABASE_URL=")]
    lines.append(f"SENTRIX_DATABASE_URL=postgresql://sentrix_app:{app_password}@127.0.0.1:55432/sentrix")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Credencial de mantenimiento local fuera del código y excluida de Git.
    (DATA.parent / "postgres-admin.env").write_text(f"SENTRIX_ADMIN_DATABASE_URL={admin_url}\n", encoding="utf-8")
    print("PostgreSQL listo en 127.0.0.1:55432; conexión guardada en backend/.env")
