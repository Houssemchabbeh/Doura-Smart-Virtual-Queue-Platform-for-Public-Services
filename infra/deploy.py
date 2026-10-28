"""Point d'entrée unique : déploie toute la plateforme Doura en une commande.

Responsable : Houssem Eddine (intégration finale : Houssem).

Usage (depuis la racine du dépôt) :
    python infra/deploy.py --stage demo
    python infra/deploy.py --stage houssem --only network,dynamodb     # tester une partie
    python infra/deploy.py --stage demo --skip-seed --skip-smoke
"""
import argparse
import sys
import time

import common
import network       # Houssem Eddine
import dynamodb      # Mouhamed
import sns           # Houssem Eddine
import s3_site       # Adem
import cognito       # Houssem
import lambdas       # Houssem
import api           # Houssem
import alarms        # Houssem Eddine
import seed_data     # Mouhamed
import smoke_test    # Houssem Eddine

# Ordre imposé par les dépendances : chaque étape lit les sorties des précédentes.
STEPS = [
    ("network", network.deploy),          # vpc_id, subnet_id, lambda_sg_id
    ("dynamodb", dynamodb.deploy),        # noms des tables, ARN du Stream tickets
    ("sns", sns.deploy),                  # ARN des topics
    ("s3_site", s3_site.deploy),          # bucket et URL du site
    ("cognito", cognito.deploy),          # user pool et client
    ("lambdas", lambdas.deploy),          # fonctions   (besoin : network, dynamodb, sns)
    ("api", api.deploy),                  # URL de l'API (besoin : lambdas, cognito)
    ("alarms", alarms.deploy),            # alarmes     (besoin : lambdas, sns)
    ("site_cfg", s3_site.upload_config),  # config.js   (besoin : api, cognito)
    ("seed", seed_data.deploy),           # données fictives (besoin : dynamodb)
    ("smoke", smoke_test.run),            # vérification finale
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Déploiement Doura sur AWS Learner Lab")
    parser.add_argument("--stage", required=True, help="préfixe des ressources : demo, ou votre prénom")
    parser.add_argument("--only", help="étapes séparées par des virgules, ex. network,dynamodb")
    parser.add_argument("--skip-seed", action="store_true", help="ne pas insérer les données de test")
    parser.add_argument("--skip-smoke", action="store_true", help="ne pas lancer le smoke test")
    args = parser.parse_args()

    if not args.stage.replace("-", "").isalnum() or args.stage != args.stage.lower():
        sys.exit("--stage : lettres minuscules, chiffres et tirets uniquement (ex. demo, houssem)")

    ctx = common.build_context(args.stage)
    run(ctx, only=set(args.only.split(",")) if args.only else None,
        skip={s for s, flag in (("seed", args.skip_seed), ("smoke", args.skip_smoke)) if flag})


def run(ctx: dict, only=None, skip=frozenset()) -> None:
    common.log("start", f"compte {ctx['account_id']}, région {ctx['region']}, stage '{ctx['stage']}'")
    started = time.time()
    for step, func in STEPS:
        if (only and step not in only) or step in skip:
            continue
        t0 = time.time()
        common.log(step, "en cours...")
        try:
            result = func(ctx) or {}
        except Exception as exc:
            common.save_outputs(ctx)  # on garde ce qui existe : tout est idempotent, on peut relancer
            common.log(step, f"ÉCHEC : {exc}")
            sys.exit(f"Arrêt à l'étape '{step}'. Corrigez puis relancez la même commande.")
        ctx["outputs"].update(result)
        common.save_outputs(ctx)
        common.log(step, f"OK ({time.time() - t0:.0f} s)")

    common.log("done", f"terminé en {time.time() - started:.0f} s")
    for key, label in (("site_url", "Site"), ("display_url", "Écran"), ("api_url", "API")):
        if key in ctx["outputs"]:
            print(f"{label:<6}: {ctx['outputs'][key]}")


if __name__ == "__main__":
    main()
