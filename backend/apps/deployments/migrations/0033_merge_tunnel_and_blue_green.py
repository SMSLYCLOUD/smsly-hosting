# Merge migration to resolve conflicting leaf nodes on production.
#
# Branch A: 0028_alter_service_is_public → 0029_tunnel_enhancements
# Branch B: ... → 0030_merge_0028_0029 → 0031_relax_health_defaults → 0032_staged_blue_green
#
# Both branches applied on VPS, creating two leaf nodes.
# This migration unifies them so Django sees a single graph tip.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0029_tunnel_enhancements"),
        ("deployments", "0032_staged_blue_green"),
    ]

    operations = [
    ]
