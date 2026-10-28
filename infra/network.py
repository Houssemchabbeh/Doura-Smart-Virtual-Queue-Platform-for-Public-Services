"""Réseau : un VPC, une zone de disponibilité, un sous-réseau privé et des endpoints de passerelle.

Responsable : Houssem Eddine (relecture : Houssem).
Sorties : vpc_id, subnet_id, route_table_id, lambda_sg_id.

Choix :
- Une seule AZ (décision d'architecture du projet).
- Pas de NAT gateway : les Lambdas du VPC joignent DynamoDB et S3 par des endpoints de passerelle (gratuits).
- Security group sans règle entrante : les Lambdas ne reçoivent aucun trafic réseau direct.
Idempotent : chaque ressource est retrouvée par ses tags (Name, Project, Stage) avant d'être créée.
"""
import common

VPC_CIDR = "10.20.0.0/16"
SUBNET_CIDR = "10.20.1.0/24"
PREFERRED_AZ = "us-east-1a"


def deploy(ctx: dict) -> dict:
    ec2 = ctx["session"].client("ec2")
    vpc_id = _ensure_vpc(ec2, ctx)
    subnet_id = _ensure_subnet(ec2, ctx, vpc_id)
    route_table_id = _ensure_route_table(ec2, ctx, vpc_id, subnet_id)
    for service in ("dynamodb", "s3"):
        _ensure_gateway_endpoint(ec2, ctx, vpc_id, route_table_id, service)
    sg_id = _ensure_security_group(ec2, ctx, vpc_id)
    return {"vpc_id": vpc_id, "subnet_id": subnet_id, "route_table_id": route_table_id, "lambda_sg_id": sg_id}


def _filters(ctx: dict, name: str, **extra) -> list:
    f = [{"Name": "tag:Name", "Values": [name]}, {"Name": "tag:Project", "Values": [common.PROJECT]},
         {"Name": "tag:Stage", "Values": [ctx["stage"]]}]
    f += [{"Name": k.replace("_", "-"), "Values": [v]} for k, v in extra.items()]
    return f


def _tag_spec(ctx: dict, resource_type: str, name: str) -> list:
    return [{"ResourceType": resource_type, "Tags": common.tag_list(ctx) + [{"Key": "Name", "Value": name}]}]


def _ensure_vpc(ec2, ctx: dict) -> str:
    name = common.name(ctx, "vpc")
    found = ec2.describe_vpcs(Filters=_filters(ctx, name))["Vpcs"]
    if found:
        common.log("network", f"VPC {name} existe déjà ({found[0]['VpcId']})")
        return found[0]["VpcId"]
    vpc_id = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=_tag_spec(ctx, "vpc", name))["Vpc"]["VpcId"]
    ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
    # Résolution DNS nécessaire aux endpoints AWS et aux noms de services.
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    common.log("network", f"VPC {name} créé ({vpc_id})")
    return vpc_id


def _pick_az(ec2) -> str:
    zones = [z["ZoneName"] for z in ec2.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}])["AvailabilityZones"]]
    return PREFERRED_AZ if PREFERRED_AZ in zones else zones[0]


def _ensure_subnet(ec2, ctx: dict, vpc_id: str) -> str:
    name = common.name(ctx, "private-a")
    found = ec2.describe_subnets(Filters=_filters(ctx, name, vpc_id=vpc_id))["Subnets"]
    if found:
        common.log("network", f"sous-réseau {name} existe déjà ({found[0]['SubnetId']})")
        return found[0]["SubnetId"]
    az = _pick_az(ec2)
    subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock=SUBNET_CIDR, AvailabilityZone=az,
                                  TagSpecifications=_tag_spec(ctx, "subnet", name))["Subnet"]["SubnetId"]
    common.log("network", f"sous-réseau privé {name} créé dans {az} ({subnet_id})")
    return subnet_id


def _ensure_route_table(ec2, ctx: dict, vpc_id: str, subnet_id: str) -> str:
    name = common.name(ctx, "private-rt")
    found = ec2.describe_route_tables(Filters=_filters(ctx, name, vpc_id=vpc_id))["RouteTables"]
    if found:
        rt = found[0]
        common.log("network", f"table de routage {name} existe déjà ({rt['RouteTableId']})")
    else:
        rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=_tag_spec(ctx, "route-table", name))["RouteTable"]
        rt["Associations"] = []
        common.log("network", f"table de routage {name} créée ({rt['RouteTableId']})")
    if not any(a.get("SubnetId") == subnet_id for a in rt.get("Associations", [])):
        ec2.associate_route_table(RouteTableId=rt["RouteTableId"], SubnetId=subnet_id)
        common.log("network", "table de routage associée au sous-réseau privé")
    return rt["RouteTableId"]


def _ensure_gateway_endpoint(ec2, ctx: dict, vpc_id: str, route_table_id: str, service: str) -> None:
    service_name = f"com.amazonaws.{ctx['region']}.{service}"
    found = ec2.describe_vpc_endpoints(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "service-name", "Values": [service_name]},
        {"Name": "vpc-endpoint-state", "Values": ["pending", "available"]},
    ])["VpcEndpoints"]
    if found:
        common.log("network", f"endpoint {service} existe déjà")
        return
    ec2.create_vpc_endpoint(
        VpcId=vpc_id, ServiceName=service_name, VpcEndpointType="Gateway", RouteTableIds=[route_table_id],
        TagSpecifications=_tag_spec(ctx, "vpc-endpoint", common.name(ctx, f"{service}-endpoint")),
    )
    common.log("network", f"endpoint de passerelle {service} créé")


def _ensure_security_group(ec2, ctx: dict, vpc_id: str) -> str:
    name = common.name(ctx, "lambda-sg")
    found = ec2.describe_security_groups(Filters=[
        {"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]
    if found:
        common.log("network", f"security group {name} existe déjà ({found[0]['GroupId']})")
        return found[0]["GroupId"]
    sg_id = ec2.create_security_group(
        GroupName=name, Description="Doura Lambdas - aucun trafic entrant", VpcId=vpc_id,
        TagSpecifications=_tag_spec(ctx, "security-group", name))["GroupId"]
    common.log("network", f"security group {name} créé ({sg_id}) : aucune règle entrante, sortie autorisée")
    return sg_id
