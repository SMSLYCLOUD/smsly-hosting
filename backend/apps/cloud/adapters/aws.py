"""Aws module."""
import json
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from .base import BaseCloudAdapter


class AWSAdapter(BaseCloudAdapter):
    # Optimized for African developers (low latency)
    # af-south-1: Cape Town
    # eu-west-2: London (often lower latency from Lagos than us-east-1)
    # eu-central-1: Frankfurt
    OPTIMAL_REGIONS = ['af-south-1', 'eu-west-2', 'eu-central-1', 'us-east-1']

    def __init__(self, access_key: str, secret_key: str,
                 region: str = 'af-south-1'):
        self.access_key = access_key
        self.secret_key = secret_key

        # Default to Cape Town if not specified, falling back to user pref
        self.region = region if region else 'af-south-1'

        self.session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=self.region
        )

    def authenticate(self) -> bool:
        try:
            sts = self.session.client('sts')
            sts.get_caller_identity()
            return True
        except (ClientError, NoCredentialsError):
            return False

    def pull_image(self, image: str) -> bool:
        """AWS ECR pulls are handled by Fargate service."""
        return True

    # pylint: disable=too-many-positional-arguments,arguments-differ
    def deploy_container(self, service_name: str, image: str,
                         env_vars: dict[str, str], cpu: int, memory: int, replicas: int = 1,
                         vpa_enabled: bool = True, **kwargs) -> str:
        """
        Deploys a container to ECS Fargate.
        Steps:
        1. Register Task Definition (Create TaskDef).
        2. Create/Update Service (CreateService).
        """
        ecs = self.session.client('ecs')
        logs = self.session.client('logs')
        ec2 = self.session.client('ec2')

        # Ensure log group exists
        try:
            logs.create_log_group(logGroupName=f"/ecs/{service_name}")
        except ClientError:
            pass  # Already exists

        container_def = {
            'name': service_name,
            'image': image,
            'cpu': cpu,
            'memory': memory,
            'environment': [{'name': k, 'value': v} for k, v in env_vars.items()],
            'logConfiguration': {
                'logDriver': 'awslogs',
                'options': {
                    'awslogs-group': f"/ecs/{service_name}",
                    'awslogs-region': self.region,
                    'awslogs-stream-prefix': 'ecs'
                }
            }
        }

        # Use actual Account ID for role ARN construction
        account_id = self._get_account_id()
        execution_role_arn = f"arn:aws:iam::{account_id}:role/ecsTaskExecutionRole"

        response = ecs.register_task_definition(
            family=service_name,
            networkMode='awsvpc',
            containerDefinitions=[container_def],
            requiresCompatibilities=['FARGATE'],
            cpu=str(cpu),
            memory=str(memory),
            executionRoleArn=execution_role_arn
        )
        task_def_arn = response['taskDefinition']['taskDefinitionArn']

        # --- Network Discovery ---
        # 1. Try to find Default VPC
        vpcs = ec2.describe_vpcs(
            Filters=[{'Name': 'isDefault', 'Values': ['true']}])['Vpcs']
        vpc_id = vpcs[0]['VpcId'] if vpcs else None

        if not vpc_id:
            # Fallback: Find ANY VPC
            vpcs = ec2.describe_vpcs()['Vpcs']
            if vpcs:
                vpc_id = vpcs[0]['VpcId']
            else:
                raise RuntimeError(
                    "No VPC found in this region. Please create one.")

        # 2. Get Subnets for this VPC
        subnets = ec2.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['Subnets']
        subnet_ids = [s['SubnetId'] for s in subnets]

        if not subnet_ids:
            raise RuntimeError(f"No subnets found in VPC {vpc_id}")

        # 3. Get Security Group (or create default)
        sgs = ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}, {
                                           'Name': 'group-name', 'Values': ['default']}])['SecurityGroups']
        sg_ids = [sgs[0]['GroupId']] if sgs else []

        # Create Service
        try:
            ecs.create_service(
                cluster='default',
                serviceName=service_name,
                taskDefinition=task_def_arn,
                desiredCount=replicas,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'securityGroups': sg_ids,
                        'assignPublicIp': 'ENABLED' if kwargs.get('is_public', True) else 'DISABLED'
                    }
                }
            )
        except ClientError as e:
            if 'Creation of service was not idempotent' in str(
                    e) or 'already exists' in str(e):
                ecs.update_service(
                    cluster='default',
                    service=service_name,
                    taskDefinition=task_def_arn,
                    desiredCount=replicas
                )
            else:
                raise e

        return task_def_arn

    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        """
        Deploys a Lambda function.
        """
        lambda_client = self.session.client('lambda')
        role_arn = self._ensure_lambda_role(function_name)

        try:
            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime=runtime,
                Role=role_arn,
                Handler=handler,
                Code={'ZipFile': code_zip},
                Timeout=30,
                MemorySize=128
            )
            return response['FunctionArn']
        except ClientError as e:
            if 'ResourceConflictException' in str(e):
                response = lambda_client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=code_zip
                )
                return response['FunctionArn']
            raise e

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        s3 = self.session.client('s3')
        # CreateBucketConfiguration is invalid for us-east-1
        config = {'LocationConstraint': self.region} if self.region != 'us-east-1' else {}

        s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration=config)

        if not public:
            s3.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )
        return f"arn:aws:s3:::{bucket_name}"

    def provision_database(self, db_name: str, engine: str,
                           version: str) -> str:
        rds = self.session.client('rds')
        response = rds.create_db_instance(
            DBInstanceIdentifier=db_name,
            Engine=engine,  # e.g. 'postgres'
            DBInstanceClass='db.t3.micro',
            MasterUsername='admin',
            MasterUserPassword='SecurePassword123!',
            # In prod: generate and store in Secrets Manager
            AllocatedStorage=20
        )
        return response['DBInstance']['DBInstanceArn']

    def create_vpc(self, cidr_block: str) -> str:
        ec2 = self.session.client('ec2')
        response = ec2.create_vpc(CidrBlock=cidr_block)
        vpc_id = response['Vpc']['VpcId']
        ec2.create_tags(Resources=[vpc_id], Tags=[
                        {'Key': 'Name', 'Value': 'SMSLY-VPC'}])
        return vpc_id

    def create_iam_role(self, role_name: str, policy: dict[str, Any]) -> str:
        iam = self.session.client('iam')
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }

        try:
            response = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy)
            )
            role_arn = response['Role']['Arn']

            # Attach inline policy
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-policy",
                PolicyDocument=json.dumps(policy)
            )
            return role_arn
        except ClientError as e:
            if 'EntityAlreadyExists' in str(e):
                return iam.get_role(RoleName=role_name)['Role']['Arn']
            raise

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        secrets = self.session.client('secretsmanager')
        try:
            response = secrets.create_secret(
                Name=secret_name,
                SecretString=secret_value
            )
            return response['ARN']
        except ClientError as e:
            if 'ResourceExistsException' in str(e):
                secrets.put_secret_value(
                    SecretId=secret_name,
                    SecretString=secret_value
                )
                return secrets.describe_secret(SecretId=secret_name)['ARN']
            raise

    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> list[dict]:
        cw = self.session.client('cloudwatch')
        # Simplified: Fetch CPUUtilization for an ECS Service
        response = cw.get_metric_statistics(
            Namespace='AWS/ECS',
            MetricName=metric_name,
            Dimensions=[{'Name': 'ServiceName', 'Value': resource_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=['Average']
        )
        return response['Datapoints']

    # --- New Methods ---

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        waf = self.session.client(
            'wafv2', region_name=self.region if scope == 'REGIONAL' else 'us-east-1')
        try:
            response = waf.create_web_acl(
                Name=name,
                Scope=scope,
                DefaultAction={'Allow': {}},
                VisibilityConfig={
                    'SampledRequestsEnabled': True,
                    'CloudWatchMetricsEnabled': True,
                    'MetricName': name
                },
                Rules=[
                    {
                        'Name': 'RateLimit',
                        'Priority': 1,
                        'Statement': {
                            'RateBasedStatement': {
                                'Limit': 2000,
                                'AggregateKeyType': 'IP'
                            }
                        },
                        'Action': {'Block': {}},
                        'VisibilityConfig': {
                            'SampledRequestsEnabled': True,
                            'CloudWatchMetricsEnabled': True,
                            'MetricName': f"{name}-RateLimit"
                        }
                    }
                ]
            )
            return response['Summary']['ARN']
        except ClientError as e:
            if 'WAFDuplicateItemException' in str(e):
                # Need to lookup logic here, simplified for now
                account_id = self._get_account_id()
                return f"arn:aws:wafv2:{self.region}:{account_id}:{scope.lower()}/webacl/{name}/existing"
            raise

    def issue_ssl_cert(self, domain_name: str) -> str:
        acm = self.session.client('acm')
        try:
            response = acm.request_certificate(
                DomainName=domain_name,
                ValidationMethod='DNS',
                Tags=[{'Key': 'ManagedBy', 'Value': 'SMSLY-Hosting'}]
            )
            return response['CertificateArn']
        except ClientError as e:
            raise e

    def _get_account_id(self):
        return self.session.client('sts').get_caller_identity()['Account']

    def _ensure_lambda_role(self, name):
        # Implementation to create execution role if not exists
        self._get_account_id()
        role_name = f"smsly-lambda-{name}"
        policy = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]
        }
        return self.create_iam_role(role_name, policy)
