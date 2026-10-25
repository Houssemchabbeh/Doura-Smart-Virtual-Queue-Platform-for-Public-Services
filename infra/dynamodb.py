"""DynamoDB : les 3 tables de Doura, en mode à la demande.

Responsable : Mouhamed.
Sorties : agencies_table, tickets_table, control_table, tickets_stream_arn.

Tables :
- agencies : PK agency_id, SK item (META | SERVICE#<lettre> | COUNTER#<id>).
- tickets  : PK ticket_id ; Stream NEW_AND_OLD_IMAGES (notifications).
    index WaitingQueue : PK queue_key (agence#date#service), SK waiting_since -> file d'attente.
    index AgencyCalled : PK agency_id, SK called_at -> derniers tickets appelés (écran public).
    Index « creux » : un ticket n'y figure que si l'attribut de clé existe.
- control  : PK pk -> compteurs journaliers (SEQ#...) et verrous de ticket actif (ACTIVE#...).
Idempotent : une table existante n'est pas recréée ; le Stream est activé s'il manque.
"""
from botocore.exceptions import ClientError

import common

STREAM = {"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"}


def _definitions(ctx: dict) -> dict:
    return {
        "agencies": {
            "KeySchema": [{"AttributeName": "agency_id", "KeyType": "HASH"},
                          {"AttributeName": "item", "KeyType": "RANGE"}],
            "AttributeDefinitions": [{"AttributeName": "agency_id", "AttributeType": "S"},
                                     {"AttributeName": "item", "AttributeType": "S"}],
        },
        "tickets": {
            "KeySchema": [{"AttributeName": "ticket_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in
                                     ("ticket_id", "queue_key", "waiting_since", "agency_id", "called_at")],
            "GlobalSecondaryIndexes": [
                {"IndexName": "WaitingQueue",
                 "KeySchema": [{"AttributeName": "queue_key", "KeyType": "HASH"},
                               {"AttributeName": "waiting_since", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}},
                {"IndexName": "AgencyCalled",
                 "KeySchema": [{"AttributeName": "agency_id", "KeyType": "HASH"},
                               {"AttributeName": "called_at", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}},
            ],
            "StreamSpecification": STREAM,
        },
        "control": {
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}],
        },
    }


def deploy(ctx: dict) -> dict:
    ddb = ctx["session"].client("dynamodb")
    outputs = {}
    for short, definition in _definitions(ctx).items():
        table_name = common.name(ctx, short)
        description = _describe(ddb, table_name)
        if description:
            common.log("dynamodb", f"table {table_name} existe déjà")
        else:
            ddb.create_table(TableName=table_name, BillingMode="PAY_PER_REQUEST", Tags=common.tag_list(ctx), **definition)
            ddb.get_waiter("table_exists").wait(TableName=table_name)
            common.log("dynamodb", f"table {table_name} créée")
            description = _describe(ddb, table_name)
        outputs[f"{short}_table"] = table_name

        if short == "tickets":
            if not description.get("StreamSpecification", {}).get("StreamEnabled"):
                ddb.update_table(TableName=table_name, StreamSpecification=STREAM)
                ddb.get_waiter("table_exists").wait(TableName=table_name)
                common.log("dynamodb", "Stream activé sur la table tickets")
                description = _describe(ddb, table_name)
            outputs["tickets_stream_arn"] = description["LatestStreamArn"]
    return outputs


def _describe(ddb, table_name: str):
    try:
        return ddb.describe_table(TableName=table_name)["Table"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise
