"""Initial migration for billing app."""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('deployments', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillingAccount',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4,
                    editable=False,
                    primary_key=True,
                    serialize=False)),
                ('stripe_customer_id', models.CharField(
                    blank=True,
                    max_length=255,
                    null=True)),
                ('balance', models.DecimalField(
                    decimal_places=2,
                    default=0.00,
                    max_digits=10)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='billing_account',
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='UsageRecord',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID')),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('cpu_cores', models.DecimalField(
                    decimal_places=2,
                    max_digits=4)),
                ('memory_mb', models.IntegerField()),
                ('duration_seconds', models.IntegerField(default=3600)),
                ('cost', models.DecimalField(
                    decimal_places=4,
                    default=0.0000,
                    max_digits=10)),
                ('service', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='usage_records',
                    to='deployments.service')),
            ],
        ),
    ]
