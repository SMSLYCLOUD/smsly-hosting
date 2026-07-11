# Widen CloudStorageDestination.access_key and .secret_key from max_length=255
# to max_length=500.
#
# Root cause: EncryptedCharField stores the Fernet ciphertext (base64url), not
# the plaintext.  Fernet adds ~120 bytes of overhead (version + IV + HMAC).
# A 64-char credential (e.g. Cloudflare R2 secret key) encrypts to ~220+ bytes,
# which silently exceeds the 255-char column limit and causes a database
# DataError → DRF returns 400 Bad Request on POST /api/v1/cloud-storage/.
#
# max_length=500 gives headroom for any S3-compatible provider key up to ~375
# plaintext characters before the column limit is hit.

import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0147_add_crowdsec_keys_to_platformconfig'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudstoragedestination',
            name='access_key',
            field=encrypted_model_fields.fields.EncryptedCharField(max_length=500),
        ),
        migrations.AlterField(
            model_name='cloudstoragedestination',
            name='secret_key',
            field=encrypted_model_fields.fields.EncryptedCharField(max_length=500),
        ),
    ]
