"""S3 : hébergement du site statique (pages citoyen et écran public).

Responsable : Adem.
Sorties : site_bucket, site_url, display_url.

deploy()        : crée le bucket, active l'hébergement web, autorise la lecture publique, envoie frontend/.
upload_config() : génère frontend/shared/config.js avec l'URL de l'API et les identifiants Cognito
                  (appelé après api et cognito).
Piège Learner Lab : le blocage d'accès public doit être désactivé AVANT de poser la politique publique.
"""
import json
import mimetypes

from botocore.exceptions import ClientError

import common

FRONTEND_DIR = common.REPO_ROOT / "frontend"
GENERATED = {"shared/config.js"}          # fichier produit par upload_config, jamais envoyé depuis le disque
DEFAULT_AGENCY = "AG-TUN-03"


def _bucket_name(ctx: dict) -> str:
    # Les noms de bucket sont globaux : l'identifiant du compte évite les collisions entre étudiants.
    return f"{common.name(ctx, 'site')}-{ctx['account_id']}"


def deploy(ctx: dict) -> dict:
    s3 = ctx["session"].client("s3")
    bucket = _bucket_name(ctx)
    _ensure_bucket(s3, ctx, bucket)
    _make_public_website(s3, bucket)
    count = _upload_frontend(s3, bucket)
    common.log("s3_site", f"{count} fichiers envoyés dans {bucket}")
    site_url = f"http://{bucket}.s3-website-{ctx['region']}.amazonaws.com"
    return {"site_bucket": bucket, "site_url": site_url,
            "display_url": f"{site_url}/display/index.html?agency={DEFAULT_AGENCY}"}


def upload_config(ctx: dict) -> dict:
    common.require(ctx, "site_bucket", "api_url", "user_pool_id", "user_pool_client_id")
    config = {
        "region": ctx["region"],
        "stage": ctx["stage"],
        "apiUrl": ctx["outputs"]["api_url"],
        "userPoolId": ctx["outputs"]["user_pool_id"],
        "userPoolClientId": ctx["outputs"]["user_pool_client_id"],
        "defaultAgency": DEFAULT_AGENCY,
        "refreshSeconds": 5,
    }
    body = "// Fichier généré par infra/s3_site.py - ne pas modifier à la main.\n" \
           f"window.DOURA_CONFIG = {json.dumps(config, indent=2)};\n"
    ctx["session"].client("s3").put_object(
        Bucket=ctx["outputs"]["site_bucket"], Key="shared/config.js", Body=body.encode("utf-8"),
        ContentType="application/javascript; charset=utf-8", CacheControl="no-cache")
    common.log("site_cfg", "config.js généré avec l'URL de l'API et les identifiants Cognito")
    return {}


def _ensure_bucket(s3, ctx: dict, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        common.log("s3_site", f"bucket {bucket} existe déjà")
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
    s3.create_bucket(Bucket=bucket)  # us-east-1 : pas de LocationConstraint
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": common.tag_list(ctx)})
    common.log("s3_site", f"bucket {bucket} créé")


def _make_public_website(s3, bucket: str) -> None:
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,       # pas d'ACL publiques...
        "BlockPublicPolicy": False, "RestrictPublicBuckets": False})  # ...mais une politique publique autorisée
    s3.put_bucket_website(Bucket=bucket, WebsiteConfiguration={
        "IndexDocument": {"Suffix": "index.html"}, "ErrorDocument": {"Key": "index.html"}})
    policy = {"Version": "2012-10-17", "Statement": [{
        "Sid": "PublicReadWebsite", "Effect": "Allow", "Principal": "*",
        "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*"}]}
    try:
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "AccessDenied":
            raise RuntimeError("politique publique refusée : le blocage d'accès public est peut-être activé "
                               "au niveau du compte (console S3 > Block Public Access settings for this account)") from exc
        raise
    common.log("s3_site", "hébergement web activé, lecture publique autorisée")


def _upload_frontend(s3, bucket: str) -> int:
    count = 0
    for path in sorted(FRONTEND_DIR.rglob("*")):
        key = path.relative_to(FRONTEND_DIR).as_posix()
        if not path.is_file() or key in GENERATED or key.endswith(".example.js") or path.name.startswith("."):
            continue
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        s3.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType=content_type,
                      CacheControl="no-cache")
        count += 1
    return count
