"""SNS : topic des événements de ticket et abonnement e-mail de démo.

Responsable : Houssem Eddine.
Sorties : ticket_topic_arn, alerts_topic_arn.

IMPORTANT pour la démo : un abonnement e-mail reste "PendingConfirmation" tant que le lien
reçu par e-mail n'a pas été cliqué. Confirmer AVANT la Sprint Review.

Adresse de démo : variable d'environnement DOURA_DEMO_EMAIL (ex. une boîte Gmail de l'équipe).
"""
import os

import common

DEMO_EMAIL = os.environ.get("DOURA_DEMO_EMAIL")


def deploy(ctx: dict) -> dict:
    sns = ctx["session"].client("sns")
    ticket_topic = _ensure_topic(sns, common.name(ctx, "ticket-events"), ctx)
    alerts_topic = _ensure_topic(sns, common.name(ctx, "alerts"), ctx)

    if DEMO_EMAIL:
        _ensure_email_subscription(sns, ticket_topic, DEMO_EMAIL)
        _ensure_email_subscription(sns, alerts_topic, DEMO_EMAIL)
    else:
        common.log("sns", "DOURA_DEMO_EMAIL non défini : aucun abonnement e-mail créé")

    return {"ticket_topic_arn": ticket_topic, "alerts_topic_arn": alerts_topic}


def _ensure_topic(sns, topic_name: str, ctx: dict) -> str:
    # create_topic est idempotent : renvoie l'ARN existant si le topic existe déjà.
    arn = sns.create_topic(Name=topic_name, Tags=common.tag_list(ctx))["TopicArn"]
    common.log("sns", f"topic {topic_name} prêt")
    return arn


def _ensure_email_subscription(sns, topic_arn: str, email: str) -> None:
    for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn):
        for sub in page["Subscriptions"]:
            if sub["Protocol"] == "email" and sub["Endpoint"] == email:
                state = "en attente de confirmation" if sub["SubscriptionArn"] == "PendingConfirmation" else "confirmé"
                common.log("sns", f"abonnement {email} déjà présent ({state})")
                return
    sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)
    common.log("sns", f"abonnement {email} créé : cliquer sur le lien de confirmation reçu par e-mail")
