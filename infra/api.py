"""API Gateway (REST) : routes, autorisateur Cognito, intégrations Lambda, CORS, déploiement.

Responsable : Houssem.
Sorties : api_id, api_url.

Routes (Livrable 3) : voir ROUTES ci-dessous ; « auth » = autorisateur Cognito (en-tête Authorization: <IdToken>).
Les rôles (CITIZEN, AGENT, MANAGER) et l'agence sont vérifiés DANS chaque Lambda, pas seulement ici.
Idempotent : API, autorisateur, ressources et méthodes sont retrouvés avant d'être créés ;
les intégrations et réponses de passerelle sont réécrites à l'identique ; un nouveau déploiement
est publié à chaque exécution pour appliquer les changements.
"""
import re

from botocore.exceptions import ClientError

import common

STAGE_NAME = "v1"
ROUTES = [
    # Santé et identité
    {"path": "/health", "method": "GET", "function": "health", "auth": False},
    {"path": "/me", "method": "GET", "function": "health", "auth": True},
    # Agences et services (lecture publique, écriture : responsable de l'agence)
    {"path": "/agencies", "method": "GET", "function": "agencies", "auth": False},
    {"path": "/agencies/{agency_id}", "method": "GET", "function": "agencies", "auth": False},
    {"path": "/agencies/{agency_id}/services", "method": "GET", "function": "agencies", "auth": False},
    {"path": "/agencies/{agency_id}/services", "method": "POST", "function": "agencies", "auth": True},
    {"path": "/agencies/{agency_id}/services/{letter}", "method": "PUT", "function": "agencies", "auth": True},
    {"path": "/agencies/{agency_id}/services/{letter}", "method": "DELETE", "function": "agencies", "auth": True},
    # Tickets (citoyen) et clôture (agent)
    {"path": "/tickets", "method": "POST", "function": "tickets", "auth": True},
    {"path": "/tickets/{ticket_id}", "method": "GET", "function": "tickets", "auth": True},
    {"path": "/tickets/{ticket_id}", "method": "DELETE", "function": "tickets", "auth": True},
    {"path": "/tickets/{ticket_id}", "method": "PATCH", "function": "tickets", "auth": True},
    # File d'attente (agent au guichet)
    {"path": "/agencies/{agency_id}/counters/{counter_id}/call-next", "method": "POST", "function": "queue",
     "auth": True},
    # Écran public de la salle d'attente
    {"path": "/display", "method": "GET", "function": "display", "auth": False},
]
CORS = {
    "Access-Control-Allow-Origin": "'*'",
    "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
    "Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
}


def deploy(ctx: dict) -> dict:
    common.require(ctx, "functions", "user_pool_arn")
    apigw = ctx["session"].client("apigateway")
    lam = ctx["session"].client("lambda")

    api_id = _ensure_api(apigw, ctx)
    authorizer_id = _ensure_authorizer(apigw, api_id, common.name(ctx, "cognito"), ctx["outputs"]["user_pool_arn"])
    resources = _resources(apigw, api_id)

    for route in ROUTES:
        function = ctx["outputs"]["functions"][route["function"]]
        resource_id = _ensure_resource(apigw, api_id, resources, route["path"])
        _ensure_method(apigw, api_id, resource_id, route, authorizer_id)
        apigw.put_integration(
            restApiId=api_id, resourceId=resource_id, httpMethod=route["method"], type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=f"arn:aws:apigateway:{ctx['region']}:lambda:path/2015-03-31/functions/{function['arn']}/invocations")
        _ensure_cors_preflight(apigw, api_id, resource_id)
        _ensure_invoke_permission(lam, ctx, api_id, function["name"], route)
        common.log("api", f"{route['method']} {route['path']} -> {function['name']}"
                          f"{' (Cognito)' if route['auth'] else ''}")

    # Les erreurs renvoyées par API Gateway elle-même (401 de l'autorisateur...) doivent aussi porter CORS,
    # sinon le navigateur masque la vraie erreur.
    for response_type in ("DEFAULT_4XX", "DEFAULT_5XX"):
        apigw.put_gateway_response(restApiId=api_id, responseType=response_type,
                                   responseParameters={f"gatewayresponse.header.{k}": v for k, v in CORS.items()})
    # 401 de l'autorisateur Cognito au même format d'erreur que les Lambdas.
    apigw.put_gateway_response(
        restApiId=api_id, responseType="UNAUTHORIZED", statusCode="401",
        responseParameters={f"gatewayresponse.header.{k}": v for k, v in CORS.items()},
        responseTemplates={"application/json": '{"error": {"code": "UNAUTHORIZED", "message": '
                                               '"Jeton manquant ou invalide.", "request_id": "$context.requestId"}}'})

    apigw.create_deployment(restApiId=api_id, stageName=STAGE_NAME, description=f"deploy.py stage {ctx['stage']}")
    api_url = f"https://{api_id}.execute-api.{ctx['region']}.amazonaws.com/{STAGE_NAME}"
    common.log("api", f"déployée sur {api_url}")
    return {"api_id": api_id, "api_url": api_url}


def _ensure_api(apigw, ctx: dict) -> str:
    name = common.name(ctx, "api")
    for page in apigw.get_paginator("get_rest_apis").paginate():
        for api in page["items"]:
            if api["name"] == name:
                common.log("api", f"API {name} existe déjà ({api['id']})")
                return api["id"]
    api_id = apigw.create_rest_api(name=name, description="Doura - API REST",
                                   endpointConfiguration={"types": ["REGIONAL"]}, tags=common.tags(ctx))["id"]
    common.log("api", f"API {name} créée ({api_id})")
    return api_id


def _ensure_authorizer(apigw, api_id: str, name: str, pool_arn: str) -> str:
    for authorizer in apigw.get_authorizers(restApiId=api_id).get("items", []):
        if authorizer["name"] == name:
            return authorizer["id"]
    authorizer_id = apigw.create_authorizer(
        restApiId=api_id, name=name, type="COGNITO_USER_POOLS", providerARNs=[pool_arn],
        identitySource="method.request.header.Authorization")["id"]
    common.log("api", "autorisateur Cognito créé")
    return authorizer_id


def _resources(apigw, api_id: str) -> dict:
    found = {}
    for page in apigw.get_paginator("get_resources").paginate(restApiId=api_id):
        for resource in page["items"]:
            found[resource["path"]] = resource["id"]
    return found


def _ensure_resource(apigw, api_id: str, resources: dict, path: str) -> str:
    """Crée les segments manquants d'un chemin (ex. /agencies/{agency_id}) à partir de la racine."""
    current = ""
    parent_id = resources["/"]
    for part in [p for p in path.split("/") if p]:
        current = f"{current}/{part}"
        if current not in resources:
            resources[current] = apigw.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)["id"]
        parent_id = resources[current]
    return parent_id


def _ensure_method(apigw, api_id: str, resource_id: str, route: dict, authorizer_id: str) -> None:
    wanted = "COGNITO_USER_POOLS" if route["auth"] else "NONE"
    try:
        existing = apigw.get_method(restApiId=api_id, resourceId=resource_id, httpMethod=route["method"])
        if existing.get("authorizationType") == wanted:
            return
        apigw.delete_method(restApiId=api_id, resourceId=resource_id, httpMethod=route["method"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NotFoundException":
            raise
    kwargs = {"authorizerId": authorizer_id} if route["auth"] else {}
    apigw.put_method(restApiId=api_id, resourceId=resource_id, httpMethod=route["method"],
                     authorizationType=wanted, **kwargs)


def _ensure_cors_preflight(apigw, api_id: str, resource_id: str) -> None:
    try:
        apigw.get_method(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NotFoundException":
            raise
        apigw.put_method(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS", authorizationType="NONE")
        apigw.put_integration(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS", type="MOCK",
                              requestTemplates={"application/json": '{"statusCode": 200}'})
        apigw.put_method_response(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS", statusCode="200",
                                  responseParameters={f"method.response.header.{k}": True for k in CORS})
    # Toujours réécrite (idempotent) : met à jour la liste des méthodes autorisées sur une API déjà déployée.
    apigw.put_integration_response(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS", statusCode="200",
                                   responseParameters={f"method.response.header.{k}": v for k, v in CORS.items()})


def _ensure_invoke_permission(lam, ctx: dict, api_id: str, function_name: str, route: dict) -> None:
    """Autorise API Gateway à invoquer la Lambda pour cette route ; {param} devient * dans l'ARN source."""
    clean = route["path"].strip("/").replace("/", "-").replace("{", "").replace("}", "") or "root"
    statement_id = f"apigw-{api_id}-{route['method']}-{clean}"[:100]
    arn_path = re.sub(r"\{[^}]+\}", "*", route["path"])
    source_arn = f"arn:aws:execute-api:{ctx['region']}:{ctx['account_id']}:{api_id}/*/{route['method']}{arn_path}"
    try:
        lam.add_permission(FunctionName=function_name, StatementId=statement_id, Action="lambda:InvokeFunction",
                           Principal="apigateway.amazonaws.com", SourceArn=source_arn)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":  # permission déjà présente
            raise
