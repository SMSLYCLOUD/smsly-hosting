"""Add deploy_mode, compose_file, compose_main_service, and is_public to Service."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0025_managedserver_gateway_secret"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="deploy_mode",
            field=models.CharField(
                choices=[("SINGLE", "Single Container"), ("COMPOSE", "Docker Compose")],
                default="SINGLE",
                help_text="SINGLE = one container, COMPOSE = docker-compose multi-container",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="compose_file",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Relative path to compose file (e.g. docker-compose.prod.yml)",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="compose_main_service",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Name of the primary service in compose for Traefik routing",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="is_public",
            field=models.BooleanField(
                default=True,
                help_text="If False, service is only accessible within the Docker network (no Traefik route)",
            ),
        ),
    ]
