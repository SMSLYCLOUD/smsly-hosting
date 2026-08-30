from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0189_blue_green_auto_promote'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='internal_subnet',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    "Docker bridge subnet (CIDR) for this project's scoped "
                    "network. When empty, falls back to "
                    "PlatformConfig.default_internal_subnet (default 172.30.224.0/24)."
                ),
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='use_internal_network',
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Attach this service to the project's scoped Docker bridge "
                    "for low-latency internal service-to-service traffic. "
                    "Disable to keep the service on the shared 'smsly-net' bridge only."
                ),
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='platform_internal_ip',
            field=models.GenericIPAddressField(
                blank=True, null=True,
                help_text=(
                    "Auto-populated: this service's IP on the platform-wide "
                    "shared bridge. Use it for inter-service traffic that "
                    "needs to escape the project's scope (e.g. dialing the "
                    "platform backend). Empty when use_internal_network=False."
                ),
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='default_internal_subnet',
            field=models.CharField(
                blank=True,
                default='172.30.224.0/24',
                help_text=(
                    "Default Docker bridge subnet (CIDR) for new ecosystem "
                    "projects' scoped networks. 172.30.0.0/16 is the IETF "
                    "CGNAT range. Override at the project level via "
                    "Project.internal_subnet."
                ),
                max_length=64,
            ),
        ),
    ]
