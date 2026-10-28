"""Cognito : user pool, app client, groupes et utilisateurs de test fictifs.

Responsable : Houssem.
Sorties : user_pool_id, user_pool_arn, user_pool_client_id.
Idempotent : le pool et le client sont retrouvés par leur nom ; groupes et utilisateurs ne sont créés que s'ils manquent.
"""
import os

from botocore.exceptions import ClientError

import common

GROUPS = {
    "CITIZEN": "Citoyens",
    "AGENT": "Agents de guichet",
    "MANAGER": "Responsables d'agence",
    "ADMIN": "Administrateurs de la plateforme",
}

# Données fictives uniquement (aucune vraie personne, aucun vrai CIN).
TEST_USERS = [
    {"email": "citoyen1@doura.test", "group": "CITIZEN", "agency_id": None},
    {"email": "citoyen2@doura.test", "group": "CITIZEN", "agency_id": None},
    {"email": "agent.tunis@doura.test", "group": "AGENT", "agency_id": "AG-TUN-03"},
    {"email": "agent.ariana@doura.test", "group": "AGENT", "agency_id": "AG-ARI-01"},
    {"email": "manager.tunis@doura.test", "group": "MANAGER", "agency_id": "AG-TUN-03"},
    {"email": "admin@doura.test", "group": "ADMIN", "agency_id": None},
]

# Mot de passe des comptes de démo : variable d'environnement, jamais commité.
TEST_PASSWORD = os.environ.get("DOURA_TEST_PASSWORD", "Doura#Demo2026")


def deploy(ctx: dict) -> dict:
    cip = ctx["session"].client("cognito-idp")
    pool_name = common.name(ctx, "users")

    pool_id = _find_pool(cip, pool_name)
    if pool_id:
        common.log("cognito", f"user pool {pool_name} existe déjà ({pool_id})")
    else:
        pool_id = _create_pool(cip, pool_name, ctx)
        common.log("cognito", f"user pool {pool_name} créé ({pool_id})")
    pool_arn = cip.describe_user_pool(UserPoolId=pool_id)["UserPool"]["Arn"]

    client_id = _ensure_client(cip, pool_id, common.name(ctx, "web"))
    for group, description in GROUPS.items():
        _ensure_group(cip, pool_id, group, description)
    for user in TEST_USERS:
        _ensure_user(cip, pool_id, user)

    return {"user_pool_id": pool_id, "user_pool_arn": pool_arn, "user_pool_client_id": client_id}


def _find_pool(cip, pool_name: str):
    for page in cip.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == pool_name:
                return pool["Id"]
    return None


def _create_pool(cip, pool_name: str, ctx: dict) -> str:
    response = cip.create_user_pool(
        PoolName=pool_name,
        UsernameAttributes=["email"],
        AutoVerifiedAttributes=["email"],
        Policies={"PasswordPolicy": {
            "MinimumLength": 8, "RequireUppercase": True, "RequireLowercase": True,
            "RequireNumbers": True, "RequireSymbols": True,
        }},
        # agency_id permet de vérifier dans les Lambdas qu'un agent n'agit que sur son agence.
        Schema=[{"Name": "agency_id", "AttributeDataType": "String", "Mutable": True,
                 "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "32"}}],
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": False},
        UserPoolTags=common.tags(ctx),
    )
    return response["UserPool"]["Id"]


def _ensure_client(cip, pool_id: str, client_name: str) -> str:
    for page in cip.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == client_name:
                common.log("cognito", f"app client {client_name} existe déjà")
                return client["ClientId"]
    response = cip.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=client_name,
        GenerateSecret=False,  # client web public (JavaScript dans le navigateur)
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        PreventUserExistenceErrors="ENABLED",
        AccessTokenValidity=1, IdTokenValidity=1, RefreshTokenValidity=1,
        TokenValidityUnits={"AccessToken": "hours", "IdToken": "hours", "RefreshToken": "days"},
    )
    common.log("cognito", f"app client {client_name} créé")
    return response["UserPoolClient"]["ClientId"]


def _ensure_group(cip, pool_id: str, group: str, description: str) -> None:
    try:
        cip.create_group(UserPoolId=pool_id, GroupName=group, Description=description)
        common.log("cognito", f"groupe {group} créé")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "GroupExistsException":
            raise


def _ensure_user(cip, pool_id: str, user: dict) -> None:
    email = user["email"]
    attributes = [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}]
    if user["agency_id"]:
        attributes.append({"Name": "custom:agency_id", "Value": user["agency_id"]})
    try:
        cip.admin_create_user(UserPoolId=pool_id, Username=email, UserAttributes=attributes,
                              MessageAction="SUPPRESS")  # pas d'e-mail d'invitation
        cip.admin_set_user_password(UserPoolId=pool_id, Username=email,
                                    Password=TEST_PASSWORD, Permanent=True)
        common.log("cognito", f"utilisateur de test {email} créé")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise
    # Idempotent : réaffecter un utilisateur à son groupe ne crée pas de doublon.
    cip.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=user["group"])
