# OpenAppSec enforce-mode option (Settings → Security Scanning).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0225_platformconfig_openappsec'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='openappsec_mode',
            field=models.CharField(choices=[('detect-learn', 'Shadow — detect and learn'), ('prevent', 'Enforce — block')], default='detect-learn', help_text='WAF enforcement mode. Shadow observes without blocking; enforce blocks malicious requests inline. Set from Settings → Security Scanning; applied to local_policy.yaml by the installer reconcile (takes effect on next update). Prove shadow parity first — enforce on a mis-tuned policy blocks legitimate traffic.', max_length=16),
        ),
    ]
