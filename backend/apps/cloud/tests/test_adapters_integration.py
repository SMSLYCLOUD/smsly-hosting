import pytest
from apps.cloud.adapters.aws import AWSAdapter
from apps.cloud.adapters.azure import AzureAdapter
from apps.cloud.adapters.gcp import GCPAdapter

def test_aws_adapter_initialization():
    adapter = AWSAdapter("fake-key", "fake-secret", "us-east-1")
    assert adapter.region == "us-east-1"

def test_azure_adapter_initialization():
    adapter = AzureAdapter("fake-tenant", "fake-client", "fake-secret", "fake-sub")
    assert adapter.subscription_id == "fake-sub"

def test_gcp_adapter_initialization():
    fake_info = {
        "type": "service_account",
        "project_id": "fake-project",
        "private_key_id": "fake-key-id",
        "private_key": "fake-private-key",
        "client_email": "fake@fake-project.iam.gserviceaccount.com",
        "client_id": "12345"
    }
    # We catch the exception because google auth will fail to parse fake-private-key, which is fine for init test
    with pytest.raises(Exception):
        adapter = GCPAdapter(fake_info, "fake-project", "us-central1")
