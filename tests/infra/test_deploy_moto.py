"""Test d'intégration du déploiement complet sur un AWS simulé (moto).

Responsable : Houssem (intégration).
Vérifie : toutes les étapes passent, et relancer le déploiement ne crée aucun doublon (idempotence).
Le smoke test (appels HTTP réels) n'est pas exécuté ici.
"""
import json

import boto3
import pytest
from moto import mock_aws

import common
import deploy

LAMBDA_TRUST = {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}


@pytest.fixture
def learner_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "INFRA_DIR", tmp_path)  # outputs-<stage>.json dans un dossier temporaire
    with mock_aws():
        session = boto3.Session(region_name="us-east-1")
        # Dans Learner Lab, LabRole existe déjà : on le simule.
        session.client("iam").create_role(RoleName="LabRole", AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST))
        yield session


def _deploy(session):
    ctx = common.build_context("demo", session=session)
    deploy.run(ctx, skip={"smoke"})
    return ctx


def test_full_deployment_is_idempotent(learner_lab):
    first = _deploy(learner_lab)
    second = _deploy(learner_lab)
    s = learner_lab

    for key in ("vpc_id", "subnet_id", "lambda_sg_id", "tickets_stream_arn", "site_bucket",
                "user_pool_id", "user_pool_client_id", "api_id"):
        assert first["outputs"][key] == second["outputs"][key], f"{key} a changé entre deux déploiements"

    ec2 = s.client("ec2")
    tagged = [{"Name": "tag:Project", "Values": ["doura"]}, {"Name": "tag:Stage", "Values": ["demo"]}]
    assert len(ec2.describe_vpcs(Filters=tagged)["Vpcs"]) == 1
    assert len(ec2.describe_subnets(Filters=tagged)["Subnets"]) == 1
    endpoints = ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [first["outputs"]["vpc_id"]]}])
    assert sorted(e["ServiceName"].split(".")[-1] for e in endpoints["VpcEndpoints"]) == ["dynamodb", "s3"]

    tables = [t for t in s.client("dynamodb").list_tables()["TableNames"] if t.startswith("doura-demo-")]
    assert sorted(tables) == ["doura-demo-agencies", "doura-demo-control", "doura-demo-tickets"]

    lam = s.client("lambda")
    functions = sorted(f["FunctionName"] for f in lam.list_functions()["Functions"])
    assert functions == ["doura-demo-agencies", "doura-demo-display", "doura-demo-health", "doura-demo-notifier"]
    assert lam.get_function_configuration(FunctionName="doura-demo-display")["VpcConfig"]["SubnetIds"] == \
        [first["outputs"]["subnet_id"]]
    assert not lam.get_function_configuration(FunctionName="doura-demo-notifier").get("VpcConfig", {}).get("SubnetIds")
    assert lam.get_function_configuration(FunctionName="doura-demo-health")["Role"].endswith(":role/LabRole")
    assert len(lam.list_event_source_mappings(FunctionName="doura-demo-notifier")["EventSourceMappings"]) == 1

    apis = [a for a in s.client("apigateway").get_rest_apis()["items"] if a["name"] == "doura-demo-api"]
    assert len(apis) == 1
    paths = sorted(r["path"] for r in s.client("apigateway").get_resources(restApiId=apis[0]["id"])["items"])
    assert paths == ["/", "/agencies", "/display", "/health", "/me"]

    cip = s.client("cognito-idp")
    pools = [p for p in cip.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == "doura-demo-users"]
    assert len(pools) == 1
    groups = sorted(g["GroupName"] for g in cip.list_groups(UserPoolId=pools[0]["Id"])["Groups"])
    assert groups == ["ADMIN", "AGENT", "CITIZEN", "MANAGER"]

    alarms = s.client("cloudwatch").describe_alarms(AlarmNamePrefix="doura-demo-")["MetricAlarms"]
    assert len(alarms) == 4

    objects = [o["Key"] for o in s.client("s3").list_objects_v2(Bucket=first["outputs"]["site_bucket"])["Contents"]]
    assert "index.html" in objects and "display/index.html" in objects and "shared/config.js" in objects
    assert "shared/config.example.js" not in objects
    config = s.client("s3").get_object(Bucket=first["outputs"]["site_bucket"], Key="shared/config.js")["Body"].read()
    assert first["outputs"]["api_url"].encode() in config


def test_only_option_runs_selected_steps(learner_lab):
    ctx = common.build_context("houssem", session=learner_lab)
    deploy.run(ctx, only={"network", "dynamodb"})
    assert "vpc_id" in ctx["outputs"] and "tickets_table" in ctx["outputs"]
    assert "api_url" not in ctx["outputs"]


def test_lambda_zip_is_reproducible():
    import lambdas
    assert lambdas.build_zip("notifier") == lambdas.build_zip("notifier")
