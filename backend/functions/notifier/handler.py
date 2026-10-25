"""Lambda notifier : déclenchée par le Stream DynamoDB de la table tickets.

Responsable : Mouhamed (textes des messages : Adem, voir messages.py).
Quand un ticket passe en SERVED ou CANCELLED, publie un e-mail sur le topic SNS ticket-events.
Hors VPC : elle n'a besoin que du Stream (lu par le service Lambda) et de SNS.
Erreurs partielles : renvoie batchItemFailures pour que seuls les enregistrements en échec soient rejoués.
"""
import os

import boto3
from boto3.dynamodb.types import TypeDeserializer

import doura_common as dc
from messages import build_message

SNS = boto3.client("sns")
TOPIC_ARN = os.environ["TOPIC_ARN"]
NOTIFY_ON = ("SERVED", "CANCELLED")
_deserializer = TypeDeserializer()


def _image(record: dict, key: str) -> dict:
    raw = record.get("dynamodb", {}).get(key) or {}
    return {k: _deserializer.deserialize(v) for k, v in raw.items()}


def lambda_handler(event, context):
    failures = []
    for record in event.get("Records", []):
        sequence = record.get("dynamodb", {}).get("SequenceNumber")
        try:
            if record.get("eventName") != "MODIFY":
                continue
            old, new = _image(record, "OldImage"), _image(record, "NewImage")
            status = new.get("status")
            if status not in NOTIFY_ON or status == old.get("status"):
                continue

            subject, body = build_message(status, new)
            SNS.publish(
                TopicArn=TOPIC_ARN,
                Subject=subject,
                Message=body,
                MessageAttributes={
                    "event_type": {"DataType": "String", "StringValue": f"TICKET_{status}"},
                    "agency_id": {"DataType": "String", "StringValue": str(new.get("agency_id", "unknown"))},
                },
            )
            dc.log("notification_sent", ticket=new.get("number"), agency_id=new.get("agency_id"), status=status)
        except Exception as exc:  # un enregistrement en échec ne doit pas bloquer les autres
            dc.log("notification_failed", sequence=sequence, error=str(exc))
            failures.append({"itemIdentifier": sequence})

    return {"batchItemFailures": failures}
