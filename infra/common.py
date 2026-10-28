"""Outils partagés par tous les modules de déploiement Doura.

Responsable : Houssem Eddine.
"""
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

REGION = "us-east-1"
PROJECT = "doura"
INFRA_DIR = Path(__file__).resolve().parent
REPO_ROOT = INFRA_DIR.parent


def outputs_file(stage: str) -> Path:
    """Un fichier de sorties par stage : infra/outputs-<stage>.json (ignoré par Git)."""
    return INFRA_DIR / f"outputs-{stage}.json"


def build_context(stage: str, session=None) -> dict:
    """Crée le contexte partagé ; s'arrête proprement si la session Learner Lab a expiré."""
    session = session or boto3.Session(region_name=REGION)
    try:
        account_id = session.client("sts").get_caller_identity()["Account"]
    except (ClientError, NoCredentialsError) as exc:
        sys.exit(
            "Session AWS Learner Lab expirée ou identifiants absents.\n"
            "Démarrez le lab, copiez les identifiants de 'AWS Details' dans ~/.aws/credentials, puis relancez.\n"
            f"Détail : {exc}"
        )
    return {
        "session": session,
        "stage": stage,
        "region": REGION,
        "account_id": account_id,
        "lab_role_arn": f"arn:aws:iam::{account_id}:role/LabRole",
        "outputs": load_outputs(stage),
    }


def name(ctx: dict, resource: str) -> str:
    """Nom standard d'une ressource : doura-<stage>-<resource>."""
    return f"{PROJECT}-{ctx['stage']}-{resource}"


def tags(ctx: dict) -> dict:
    """Tags communs, au format dictionnaire (Lambda, SNS via tag_list, Cognito...)."""
    return {"Project": PROJECT, "Stage": ctx["stage"]}


def tag_list(ctx: dict) -> list:
    """Tags communs, au format liste Key/Value (EC2, DynamoDB, SNS...)."""
    return [{"Key": k, "Value": v} for k, v in tags(ctx).items()]


def load_outputs(stage: str) -> dict:
    path = outputs_file(stage)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_outputs(ctx: dict) -> None:
    outputs_file(ctx["stage"]).write_text(json.dumps(ctx["outputs"], indent=2, sort_keys=True))


def require(ctx: dict, *keys: str) -> None:
    """Vérifie que les sorties des modules précédents sont présentes (message clair sinon)."""
    missing = [k for k in keys if k not in ctx["outputs"]]
    if missing:
        raise RuntimeError(f"sorties manquantes {missing} : lancez d'abord les étapes qui les produisent")


def log(step: str, message: str) -> None:
    print(f"[{step:<9}] {message}", flush=True)
