"""Test Templates Unit module."""
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

        cmd = get_docker_run_command(
            'smsly-sms', name='my-sms', domain='example.com')
        self.assertIn('docker run', cmd)
        self.assertIn('--name my-sms', cmd)
        self.assertIn('-p 8000:8000', cmd)
        self.assertIn('smslycloud/sms:latest', cmd)

import pytest
from unittest.mock import patch, MagicMock
from apps.deployments.models import Service, EnvironmentVariable
from apps.cloud.models import CloudProvider
from django.contrib.auth import get_user_model
import json

User = get_user_model()

@pytest.mark.django_db
@patch('apps.deployments.tasks.smart_deploy_task.delay')
@patch('apps.deployments.tasks.subprocess.run')
@patch('apps.deployments.tasks.addon_provisioner.provision')
def test_all_ai_templates_dry_run(mock_addon_provisioner, mock_subprocess_run, mock_smart_deploy):
    mock_addon_provisioner.return_value = ('test-uuid', 'postgres://...')
    from apps.deployments.tasks import one_click_deploy_template_task

    # Mock subprocess.run to avoid docker manifest inspect calls
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    user = User.objects.create_user(username='test_user', password='password123')
    local_provider = CloudProvider.objects.create(
        name='Local Docker',
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True,
    )

    with open('backend/apps/deployments/fixtures/templates.json', 'r') as f:
        templates = json.load(f)

    ai_templates = [t for t in templates if t.get('category') == 'intelligence']
    assert len(ai_templates) > 0, "No AI templates found in fixtures!"

    for t in ai_templates:
        service = Service.objects.create(
            name=f"test-{t['id']}",
            deploy_type='DOCKER',
            docker_image=t.get('docker_image', 'nginx:latest'),
            internal_port=t.get('default_port', 8000),
            owner=user,
            provider=local_provider,
        )

        # Test the deployment task logic without errors
        try:
            one_click_deploy_template_task(str(service.id), t['id'])
        except Exception as e:
            pytest.fail(f"Template deployment failed for {t['id']}: {e}")

        # specifically check ai-router created companions
        if t['id'] == 'ai-router':
            companions = Service.objects.filter(owner=user).exclude(id=service.id)
            # Should have created 3 companion services: llama-3-2, qwen2.5-0.5b, ollama-nomic-embed-text
            assert companions.count() == 3, f"Expected 3 companions for ai-router, got {companions.count()}"
            companion_names = [c.name for c in companions]
            assert any('llama-3-2' in name for name in companion_names)
            assert any('qwen2-5-0-5b' in name for name in companion_names)
            assert any('ollama-nomic-embed-text' in name for name in companion_names)

            # Check router env var
            env_var = EnvironmentVariable.objects.filter(service=service, key='AI_ROUTER_SELECTED_SERVICE_IDS').first()
            assert env_var is not None
            assert len(json.loads(env_var.value)) == 3

        # Clean up
        EnvironmentVariable.objects.filter(service__owner=user).delete()
        Service.objects.filter(owner=user).delete()
