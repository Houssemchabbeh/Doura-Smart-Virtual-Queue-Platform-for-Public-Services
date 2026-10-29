"""Outils des tests d'API : AWS simulé avec données de seed, événements API Gateway, utilisateurs Cognito fictifs.

Responsable : Houssem.
"""
import json
from contextlib import contextmanager

import boto3
from moto import mock_aws

import dynamodb
import seed_data
from conftest import load_handler

CIN = "01234567"  # CIN fictif
CITIZEN = {"sub": "sub-citoyen-1", "cognito:groups": "CITIZEN", "email": "citoyen1@doura.test"}
CITIZEN_2 = {"sub": "sub-citoyen-2", "cognito:groups": "CITIZEN", "email": "citoyen2@doura.test"}
AGENT_TUN = {"sub": "sub-agent-tun", "cognito:groups": "AGENT", "custom:agency_id": "AG-TUN-03"}
AGENT_ARI = {"sub": "sub-agent-ari", "cognito:groups": "AGENT", "custom:agency_id": "AG-ARI-01"}
MANAGER_TUN = {"sub": "sub-manager-tun", "cognito:groups": "MANAGER", "custom:agency_id": "AG-TUN-03"}
MANAGER_ARI = {"sub": "sub-manager-ari", "cognito:groups": "MANAGER", "custom:agency_id": "AG-ARI-01"}


@contextmanager
def seeded_aws(*functions):
    """Tables + données de seed dans moto ; renvoie (outputs, {fonction: module handler})."""
    with mock_aws():
        session = boto3.Session(region_name="us-east-1")
        ctx = {"session": session, "stage": "test", "region": "us-east-1", "account_id": "123456789012", "outputs": {}}
        ctx["outputs"].update(dynamodb.deploy(ctx))
        ctx["outputs"].update(seed_data.deploy(ctx))
        out = ctx["outputs"]
        env = {"AGENCIES_TABLE": out["agencies_table"], "TICKETS_TABLE": out["tickets_table"],
               "CONTROL_TABLE": out["control_table"], "CIN_SALT": "sel-de-test"}
        yield out, {name: load_handler(name, env) for name in functions}


def event(resource, method, path=None, body=None, user=None, query=None):
    """Événement API Gateway (intégration proxy) ; `user` = claims Cognito ajoutés par l'autorisateur."""
    return {
        "resource": resource, "httpMethod": method, "pathParameters": path, "queryStringParameters": query,
        "body": body if body is None or isinstance(body, str) else json.dumps(body),
        "requestContext": {"authorizer": {"claims": user}} if user else {},
    }


class Context:
    aws_request_id = "test-request-id"


def call(handler, ev):
    """Appelle la Lambda ; vérifie qu'aucune donnée de CIN ne fuit ; renvoie (statut, corps décodé)."""
    resp = handler.lambda_handler(ev, Context())
    raw = resp["body"]
    assert '"cin' not in raw and CIN not in raw, f"fuite de CIN dans la réponse : {raw}"
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    return resp["statusCode"], (json.loads(raw) if raw else None)


def error_code(body) -> str:
    return body["error"]["code"]
