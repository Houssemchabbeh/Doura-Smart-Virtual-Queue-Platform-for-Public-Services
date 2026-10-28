"""Textes des e-mails envoyés aux citoyens.

Responsable : Adem.
Règle : seul le numéro de ticket identifie le citoyen ; aucune donnée personnelle (nom, CIN, téléphone).
L'objet reste en ASCII (sans accents) pour être compatible avec tous les clients e-mail.
"""

TEMPLATES = {
    "SERVED": {
        "subject": "Doura - Ticket {number} servi",
        "body": (
            "Bonjour,\n\n"
            "Votre ticket {number} pour le service {service} a été servi au guichet {counter_id}.\n"
            "Agence : {agency_name}\n"
            "Date : {when}\n\n"
            "Merci d'avoir utilisé Doura.\n"
            "Ce message est automatique, merci de ne pas y répondre."
        ),
    },
    "CANCELLED": {
        "subject": "Doura - Ticket {number} annule",
        "body": (
            "Bonjour,\n\n"
            "Votre ticket {number} pour le service {service} a été annulé.\n"
            "Agence : {agency_name}\n"
            "Date : {when}\n\n"
            "Vous pouvez reprendre un ticket à tout moment depuis l'application Doura.\n"
            "Ce message est automatique, merci de ne pas y répondre."
        ),
    },
}


def build_message(status: str, ticket: dict) -> tuple:
    """Renvoie (objet, corps) pour un ticket passé au statut donné."""
    template = TEMPLATES[status]
    values = {
        "number": ticket.get("number", "?"),
        "service": ticket.get("service", "?"),
        "counter_id": ticket.get("counter_id") or "-",
        "agency_name": ticket.get("agency_name") or ticket.get("agency_id", "?"),
        "when": ticket.get("served_at") or ticket.get("cancelled_at") or ticket.get("updated_at") or "",
    }
    return template["subject"].format(**values), template["body"].format(**values)
