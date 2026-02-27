# Merge migration to resolve conflicting leaf nodes across VPS instances.
#
# Some VPSes applied 0028_alter_service_is_public (just is_public change),
# while the repo later created 0028_environmentvariable_source_alter_service_is_public
# (is_public + env source) followed by 0029_platformupdate.
#
# This merge unifies ALL THREE branches so both old and new VPSes converge.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0028_alter_service_is_public"),
        ("deployments", "0028_environmentvariable_source_alter_service_is_public"),
        ("deployments", "0029_platformupdate"),
    ]

    operations = [
    ]
