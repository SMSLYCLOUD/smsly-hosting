from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge migration resolving parallel heads:
      - 0002_service_public_domain_hidden
      - 0040_alter_managedserver_api_token_and_more
    """

    dependencies = [
        ("deployments", "0040_alter_managedserver_api_token_and_more"),
        ("deployments", "0002_service_public_domain_hidden"),
    ]

    operations = []
