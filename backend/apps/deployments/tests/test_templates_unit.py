from django.test import SimpleTestCase
from services.app_templates import list_templates, get_template, APP_TEMPLATES

class TemplateRegistryTest(SimpleTestCase):
    def test_smsly_templates_exist(self):
        """Verify SMSLY ecosystem templates are registered."""
        sms_template = get_template('smsly-sms')
        self.assertIsNotNone(sms_template)
        self.assertEqual(sms_template.default_port, 8000)

        voice_template = get_template('smsly-voice')
        self.assertIsNotNone(voice_template)
        self.assertEqual(voice_template.default_port, 3000)

        platform_template = get_template('smsly-platform-api')
        self.assertIsNotNone(platform_template)
        self.assertEqual(platform_template.default_port, 8080)

    def test_list_templates_filter(self):
        """Verify filtering by category."""
        ecosystem = list_templates(category='smsly-ecosystem')
        self.assertTrue(len(ecosystem) >= 4)
        for t in ecosystem:
            self.assertEqual(t.category, 'smsly-ecosystem')

    def test_docker_run_command_generation(self):
        """Verify command generation (importing function inside test to avoid circular imports if any)."""
        from services.app_templates import get_docker_run_command

        cmd = get_docker_run_command('smsly-sms', name='my-sms', domain='example.com')
        self.assertIn('docker run', cmd)
        self.assertIn('--name my-sms', cmd)
        self.assertIn('-p 8000:8000', cmd)
        self.assertIn('smslycloud/sms:latest', cmd)
