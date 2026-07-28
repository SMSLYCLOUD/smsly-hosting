from django.test import TestCase

from apps.autoscaler.models import AutoscalerConfig


class AutoscalerConfigTests(TestCase):
    def test_create_config(self):
        config = AutoscalerConfig.objects.create(data={"min_replicas": 1, "max_replicas": 5})
        self.assertIsNotNone(config.pk)
        self.assertEqual(config.data["min_replicas"], 1)

    def test_str(self):
        config = AutoscalerConfig.objects.create(data={})
        self.assertIn("Autoscaler Config", str(config))
        self.assertIn("Updated:", str(config))

    def test_get_config_creates_default(self):
        AutoscalerConfig.objects.all().delete()
        data = AutoscalerConfig.get_config()
        self.assertEqual(data, {})
        self.assertEqual(AutoscalerConfig.objects.count(), 1)

    def test_save_config_overwrites(self):
        AutoscalerConfig.save_config({"key": "value"})
        AutoscalerConfig.save_config({"key": "new_value"})
        self.assertEqual(AutoscalerConfig.get_config()["key"], "new_value")
