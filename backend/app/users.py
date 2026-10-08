"""Usuarios mínimos. Alta por CLI y verificación de credenciales por HTTP."""
import argparse
import getpass
import os

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from app.database import connect

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=12, max_length=256)


class UserStore:
    def __init__(self, target):
        self.target = target
        self.dummy_hash = hasher.hash(os.urandom(32).hex())

    def connect(self):
        connection = connect(self.target)
        connection.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL
        )""")
        return connection

    def create(self, username, password):
        credentials = Credentials(username=username, password=password)
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute("INSERT INTO users VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (credentials.username.lower(), hasher.hash(credentials.password.get_secret_value())))
                if cursor.rowcount != 1:
                    raise ValueError("El usuario ya existe")
        finally:
            connection.close()
        return {"username": credentials.username.lower()}

    def verify(self, username, password):
        connection = self.connect()
        try:
            with connection:
                row = connection.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
                try:
                    hasher.verify(row["password_hash"] if row else self.dummy_hash, password)
                except VerificationError:
                    return None
                if row is None:
                    return None
                if hasher.check_needs_rehash(row["password_hash"]):
                    connection.execute("UPDATE users SET password_hash=? WHERE username=?",
                                       (hasher.hash(password), row["username"]))
                return {"username": row["username"]}
        finally:
            connection.close()


def users_router(target):
    store = UserStore(target)
    router = APIRouter(prefix="/auth", tags=["Usuarios"])

    @router.post("/verify")
    def verify(credentials: Credentials):
        try:
            user = store.verify(credentials.username, credentials.password.get_secret_value())
        except Exception:
            raise HTTPException(503, "Almacenamiento de usuarios no disponible") from None
        if user is None:
            raise HTTPException(401, "Usuario o contraseña incorrectos")
        return user

    return router


if __name__ == "__main__":
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Crear usuario; contraseña solicitada sin mostrarla")
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
    if password != getpass.getpass("Repetir contraseña: "):
        parser.error("Las contraseñas no coinciden")
    url = os.environ.get("SENTRIX_DATABASE_URL")
    if not url:
        parser.error("Configurar SENTRIX_DATABASE_URL")
    UserStore(url).create(args.username, password)
    print("Usuario creado")
