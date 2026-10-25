"""Module partagé par toutes les Lambdas Doura (copié dans chaque zip au déploiement).

Responsable : Mouhamed.
Contenu : réponses HTTP avec CORS, format d'erreur unique, journalisation JSON sans CIN,
lecture des claims Cognito.
"""
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger("doura")
logger.setLevel(logging.INFO)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

# Champs jamais journalisés, même par erreur.
_SENSITIVE_KEYS = ("cin", "cin_hash", "password", "token", "authorization")


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Type non sérialisable : {type(value).__name__}")


def response(status: int, body=None) -> dict:
    """Réponse au format Lambda proxy d'API Gateway."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8", **CORS_HEADERS},
        "body": "" if body is None else json.dumps(body, default=_json_default, ensure_ascii=False),
    }


def error(status: int, code: str, message: str, request_id: str = None) -> dict:
    """Format d'erreur unique défini dans le contrat d'API."""
    return response(status, {"error": {"code": code, "message": message, "request_id": request_id}})


def mask(data):
    """Copie d'un dict/list avec les champs sensibles masqués."""
    if isinstance(data, dict):
        return {k: ("***" if any(s in k.lower() for s in _SENSITIVE_KEYS) else mask(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [mask(v) for v in data]
    return data


def log(event: str, **fields) -> None:
    """Journal JSON structuré, lisible dans CloudWatch Logs Insights."""
    logger.info(json.dumps({"event": event, **mask(fields)}, default=_json_default, ensure_ascii=False))


def claims(event: dict) -> dict:
    """Claims Cognito ajoutés par l'autorisateur d'API Gateway (vide si route publique)."""
    return (event.get("requestContext") or {}).get("authorizer", {}).get("claims", {}) or {}


def groups(event: dict) -> list:
    raw = claims(event).get("cognito:groups", "")
    if isinstance(raw, list):
        return raw
    return [g for g in str(raw).replace("[", "").replace("]", "").replace(",", " ").split() if g]


def query_param(event: dict, name: str, default=None):
    return (event.get("queryStringParameters") or {}).get(name, default)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request_id(context) -> str:
    return getattr(context, "aws_request_id", None)
