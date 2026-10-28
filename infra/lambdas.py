"""Lambdas de démo du Sprint 1 : packaging et déploiement.

Responsable : Houssem (code métier des fonctions : Mouhamed, dans backend/functions/).
Sorties : functions = {clé: {"name": ..., "arn": ...}}.

- Chaque zip contient handler.py (+ autres .py du dossier de la fonction) et backend/common/doura_common.py.
- Zip déterministe : même code -> même empreinte -> aucune mise à jour inutile (idempotence).
- LabRole uniquement (aucun rôle IAM créé).
- health, agencies, display : dans le sous-réseau privé (DynamoDB via endpoint de passerelle).
- notifier : hors VPC (lit le Stream et publie sur SNS ; évite un endpoint SNS payant) + déclencheur Stream.
"""
import base64
import hashlib
import io
import zipfile

from botocore.exceptions import ClientError

import common

BACKEND = common.REPO_ROOT / "backend"
RUNTIME = "python3.12"
HANDLER = "handler.lambda_handler"

FUNCTIONS = {
    "health":   {"vpc": True,  "timeout": 5,  "memory": 128, "env": {}},
    "agencies": {"vpc": True,  "timeout": 10, "memory": 128, "env": {"AGENCIES_TABLE": "agencies_table"}},
    "display":  {"vpc": True,  "timeout": 10, "memory": 128,
                 "env": {"TICKETS_TABLE": "tickets_table", "AGENCIES_TABLE": "agencies_table"}},
    "notifier": {"vpc": False, "timeout": 15, "memory": 128, "env": {"TOPIC_ARN": "ticket_topic_arn"},
                 "stream": "tickets_stream_arn"},
}


def deploy(ctx: dict) -> dict:
    common.require(ctx, "subnet_id", "lambda_sg_id", "agencies_table", "tickets_table",
                   "tickets_stream_arn", "ticket_topic_arn")
    lam = ctx["session"].client("lambda")
    functions = {}
    for key, spec in FUNCTIONS.items():
        name = common.name(ctx, key)
        zip_bytes = build_zip(key)
        config = _desired_config(ctx, name, spec)
        arn = _ensure_function(lam, ctx, name, zip_bytes, config)
        if spec.get("stream"):
            _ensure_stream_trigger(lam, name, ctx["outputs"][spec["stream"]])
        functions[key] = {"name": name, "arn": arn}
    return {"functions": functions}


def build_zip(key: str) -> bytes:
    """Zip reproductible : dates fixes et ordre trié."""
    files = sorted((BACKEND / "functions" / key).glob("*.py")) + sorted((BACKEND / "common").glob("*.py"))
    if not (BACKEND / "functions" / key / "handler.py").exists():
        raise RuntimeError(f"backend/functions/{key}/handler.py introuvable")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _desired_config(ctx: dict, name: str, spec: dict) -> dict:
    env = {"STAGE": ctx["stage"]}
    env.update({var: ctx["outputs"][out] for var, out in spec["env"].items()})
    config = {
        "FunctionName": name, "Runtime": RUNTIME, "Handler": HANDLER, "Role": ctx["lab_role_arn"],
        "Timeout": spec["timeout"], "MemorySize": spec["memory"], "Environment": {"Variables": env},
        "VpcConfig": {"SubnetIds": [ctx["outputs"]["subnet_id"]], "SecurityGroupIds": [ctx["outputs"]["lambda_sg_id"]]}
        if spec["vpc"] else {"SubnetIds": [], "SecurityGroupIds": []},
    }
    return config


def _sha256_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _ensure_function(lam, ctx: dict, name: str, zip_bytes: bytes, config: dict) -> str:
    try:
        current = lam.get_function_configuration(FunctionName=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        current = None

    if current is None:
        created = lam.create_function(Code={"ZipFile": zip_bytes}, Tags=common.tags(ctx), Publish=False, **config)
        # Une Lambda dans un VPC met parfois plus d'une minute à devenir active (création des interfaces réseau).
        lam.get_waiter("function_active_v2").wait(FunctionName=name)
        common.log("lambdas", f"{name} créée")
        return created["FunctionArn"]

    if current.get("CodeSha256") != _sha256_b64(zip_bytes):
        lam.update_function_code(FunctionName=name, ZipFile=zip_bytes)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)  # sinon ResourceConflictException
        common.log("lambdas", f"{name} : code mis à jour")
    if _config_differs(current, config):
        update = {k: v for k, v in config.items() if k != "FunctionName"}
        lam.update_function_configuration(FunctionName=name, **update)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        common.log("lambdas", f"{name} : configuration mise à jour")
    else:
        common.log("lambdas", f"{name} à jour")
    return current["FunctionArn"]


def _config_differs(current: dict, desired: dict) -> bool:
    vpc = current.get("VpcConfig") or {}
    return any([
        current.get("Runtime") != desired["Runtime"],
        current.get("Handler") != desired["Handler"],
        current.get("Role") != desired["Role"],
        current.get("Timeout") != desired["Timeout"],
        current.get("MemorySize") != desired["MemorySize"],
        (current.get("Environment") or {}).get("Variables", {}) != desired["Environment"]["Variables"],
        sorted(vpc.get("SubnetIds") or []) != sorted(desired["VpcConfig"]["SubnetIds"]),
        sorted(vpc.get("SecurityGroupIds") or []) != sorted(desired["VpcConfig"]["SecurityGroupIds"]),
    ])


def _ensure_stream_trigger(lam, function_name: str, stream_arn: str) -> None:
    existing = lam.list_event_source_mappings(FunctionName=function_name, EventSourceArn=stream_arn)
    if existing.get("EventSourceMappings"):
        common.log("lambdas", f"déclencheur Stream de {function_name} existe déjà")
        return
    lam.create_event_source_mapping(
        FunctionName=function_name, EventSourceArn=stream_arn, StartingPosition="LATEST",
        BatchSize=10, MaximumRetryAttempts=3, FunctionResponseTypes=["ReportBatchItemFailures"])
    common.log("lambdas", f"déclencheur Stream tickets -> {function_name} créé")
