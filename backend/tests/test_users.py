import pytest
from app.users import UserStore, Credentials
from pydantic import ValidationError


def test_password_hash_and_restart(tmp_path):
    target = tmp_path / "users.sqlite3"
    store = UserStore(target)
    password = "Una-contraseña-segura-123"
    assert store.create("Operador", password) == {"username": "operador"}
    connection = store.connect()
    try:
        row = connection.execute("SELECT * FROM users").fetchone()
        assert row["password_hash"].startswith("$argon2id$")
        assert password not in row["password_hash"]
    finally:
        connection.close()
    restarted = UserStore(target)
    assert restarted.verify("OPERADOR", password) == {"username": "operador"}
    assert restarted.verify("operador", "incorrecta") is None
    assert restarted.verify("inexistente", password) is None
    with pytest.raises(ValueError):
        restarted.create("OPERADOR", password)


def test_credentials_validation():
    with pytest.raises(ValidationError):
        Credentials(username="usuario", password="corta")
    with pytest.raises(ValidationError):
        Credentials(username="usuario", password="larga-y-segura-123", email="a@b.com")
