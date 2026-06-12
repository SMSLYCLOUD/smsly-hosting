# Generated for LLMUsage and UserAICap models

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('intelligence', '0009_aiprovidersettings_mistral_nvidia_cloudflare'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='jules_auto_deploy_pr',
            field=models.BooleanField(default=False, help_text='Automatically redeploy services from the branch of Jules auto-fix PRs'),
        ),
        migrations.CreateModel(
            name='LLMUsage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(max_length=64)),
                ('model', models.CharField(blank=True, max_length=128)),
                ('prompt_tokens', models.IntegerField(default=0)),
                ('completion_tokens', models.IntegerField(default=0)),
                ('total_tokens', models.IntegerField(default=0)),
                ('estimated_cost_usd', models.DecimalField(decimal_places=6, default=0, max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='llm_usages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['user', '-created_at'], name='intelligence_user_id_39e0a9_idx')],
            },
        ),
        migrations.CreateModel(
            name='UserAICap',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('daily_token_cap', models.IntegerField(default=100000)),
                ('daily_cost_cap_usd', models.DecimalField(decimal_places=2, default=10.0, max_digits=8)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_cap', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
