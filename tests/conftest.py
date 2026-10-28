"""Configuration commune des tests (pytest + moto, aucun appel réel à AWS).

Responsable : Houssem Eddine.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))
sys.path.insert(0, str(ROOT / "backend" / "common"))

# Faux identifiants : garantit qu'aucun test ne touche un vrai compte AWS.
for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SECURITY_TOKEN": "testing", "AWS_SESSION_TOKEN": "testing",
                   "AWS_DEFAULT_REGION": "us-east-1"}.items():
    os.environ[key] = value


def load_handler(function: str, env: dict):
    """Importe backend/functions/<function>/handler.py sous un nom unique, avec ses variables d'environnement."""
    os.environ.update(env)
    folder = ROOT / "backend" / "functions" / function
    sys.path.insert(0, str(folder))
    try:
        for helper in ("messages",):  # modules locaux d'une fonction : recharger depuis le bon dossier
            sys.modules.pop(helper, None)
        spec = importlib.util.spec_from_file_location(f"handler_{function}", folder / "handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(folder))


@pytest.fixture
def lambda_context():
    class Context:
        aws_request_id = "test-request-id"
    return Context()
