from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mtls', '0009_alter_mtlsconfig_trust_domain'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mtlsconfig',
            name='enabled',
            field=models.BooleanField(
                default=False,
                help_text='Master switch for mTLS on this service. Off by default for non-ecosystem services; ecosystem deploy and platform services enable their own rows explicitly.',
            ),
        ),
        migrations.AlterField(
            model_name='mtlsconfig',
            name='sidecar_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Enable Envoy sidecar for transparent mTLS. When enabled, an Envoy proxy is deployed alongside the service to handle mTLS termination/origination transparently. Off by default for non-ecosystem services.',
            ),
        ),
    ]
