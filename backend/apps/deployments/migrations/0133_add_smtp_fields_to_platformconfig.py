"""
Add SMTP fields to PlatformConfig.
"""
from django.db import migrations, models
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0132_ecosystemplan_use_shared_addons'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_host',
            field=models.CharField(blank=True, default='', help_text='SMTP server host (e.g. smtp.gmail.com)', max_length=255),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_port',
            field=models.PositiveIntegerField(default=587, help_text='SMTP server port (default 587 for STARTTLS)'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_username',
            field=models.CharField(blank=True, default='', help_text='SMTP authentication username', max_length=255),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_password',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', help_text='SMTP authentication password', max_length=512),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_use_tls',
            field=models.BooleanField(default=True, help_text='Enable STARTTLS encryption'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_from_email',
            field=models.CharField(blank=True, default='', help_text='Default from address for outgoing emails', max_length=255),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smtp_from_name',
            field=models.CharField(blank=True, default='SMSLY', help_text='Default from name for outgoing emails', max_length=100),
        ),
    ]
