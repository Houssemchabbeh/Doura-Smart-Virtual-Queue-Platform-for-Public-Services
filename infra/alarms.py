"""CloudWatch : une alarme d'erreurs par Lambda, avec alerte e-mail sur le topic alerts.

Responsable : Houssem Eddine.
Seuil : au moins 1 erreur sur 5 minutes. Pas de données = OK (fonction non appelée).
Idempotent : put_metric_alarm remplace l'alarme du même nom.
"""
import common


def deploy(ctx: dict) -> dict:
    common.require(ctx, "functions", "alerts_topic_arn")
    cw = ctx["session"].client("cloudwatch")
    names = []
    for key, function in ctx["outputs"]["functions"].items():
        alarm_name = common.name(ctx, f"{key}-errors")
        cw.put_metric_alarm(
            AlarmName=alarm_name,
            AlarmDescription=f"Erreurs de la Lambda {function['name']}",
            Namespace="AWS/Lambda", MetricName="Errors",
            Dimensions=[{"Name": "FunctionName", "Value": function["name"]}],
            Statistic="Sum", Period=300, EvaluationPeriods=1, Threshold=1,
            ComparisonOperator="GreaterThanOrEqualToThreshold", TreatMissingData="notBreaching",
            AlarmActions=[ctx["outputs"]["alerts_topic_arn"]],
            Tags=common.tag_list(ctx),
        )
        names.append(alarm_name)
    common.log("alarms", f"{len(names)} alarmes d'erreurs configurées")
    return {"alarms": names}
