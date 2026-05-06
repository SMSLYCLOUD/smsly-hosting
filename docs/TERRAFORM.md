# Infrastructure as Code (Terraform)

Grid supports automated provisioning of cloud infrastructure using Terraform.

## Usage

Navigate to the `infrastructure/terraform` directory to find modules for major cloud providers.

### AWS Setup
```hcl
module "Grid_aws" {
  source = "./modules/aws"
  region = "us-east-1"

  # Configuration options
  instance_type = "t3.medium"
  domain        = "cloud.mycompany.com"
}
```

Deploying:
```bash
cd infrastructure/terraform/aws
terraform init
terraform apply
```

The modules configure VPC, Subnets, Security Groups, IAM Roles, and an EC2 instance pre-configured with the Grid platform via User Data script.
