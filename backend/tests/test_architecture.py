"""Dependency boundaries and model resources must survive future moves."""
import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_do_not_import_adapters_or_web_frameworks(layer):
    forbidden = ("app.infrastructure", "app.presentation", "app.bootstrap", "app.main",
                 "fastapi", "starlette", "aiortc", "av", "psycopg", "sqlite3", "dotenv")
    for path in (APP / layer).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"Use explicit imports so the boundary can be checked: {path}"
                imports = [node.module or ""]
            else:
                continue
            if layer == "domain":
                assert not any(name.startswith("app.application") for name in imports), path
            assert not any(name == prefix or name.startswith(prefix + ".")
                           for name in imports for prefix in forbidden), (path, imports)


def test_videomae_still_resolves_existing_model_directory():
    from app.infrastructure.inference.videomae import MODEL_ROOT, load_metadata
    assert MODEL_ROOT == APP.parents[1] / "modelo"
    assert load_metadata()["input"]["shape"] == [1, 3, 16, 224, 224]
