"""Test Templates Unit module."""
import ast
from pathlib import Path

from django.test import SimpleTestCase
from apps.deployments.services.app_templates import APP_TEMPLATES, get_template, list_templates


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
        from apps.deployments.services.app_templates import get_docker_run_command

        cmd = get_docker_run_command(
            'smsly-sms', name='my-sms', domain='example.com')
        self.assertIn('docker run', cmd)
        self.assertIn('--name my-sms', cmd)
        self.assertIn('-p 8000:8000', cmd)
        self.assertIn('smslycloud/sms:latest', cmd)

    def test_template_required_addons_are_supported_by_provisioner(self):
        """Ensure every template-required addon is recognized by addon provisioner."""
        provisioner_path = (
            Path(__file__).resolve().parents[3] / 'services' / 'addon_provisioner.py'
        )
        module = ast.parse(provisioner_path.read_text(encoding='utf-8'))
        addon_images = {}
        generic_addons = {}

        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name == 'AddonProvisioner':
                for class_node in node.body:
                    if isinstance(class_node, ast.Assign):
                        for target in class_node.targets:
                            if isinstance(target, ast.Name) and target.id == 'ADDON_IMAGES':
                                addon_images = ast.literal_eval(class_node.value)
                            if isinstance(target, ast.Name) and target.id == 'GENERIC_ADDONS_CONFIG':
                                generic_addons = ast.literal_eval(class_node.value)

        supported_addons = set(addon_images.keys()) | set(generic_addons.keys())
        self.assertTrue(supported_addons)

        for template in APP_TEMPLATES.values():
            for addon in template.required_addons:
                self.assertIn(
                    addon,
                    supported_addons,
                    msg=f"Template '{template.id}' requires unsupported addon '{addon}'",
                )

import json  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from apps.cloud.models import CloudProvider  # noqa: E402
from apps.deployments.models import EnvironmentVariable, Service  # noqa: E402

User = get_user_model()

@pytest.mark.django_db
@patch('apps.deployments.tasks.smart_deploy_task.delay')
@patch('apps.deployments.tasks.deployment.tasks_templates.subprocess.run')
@patch('apps.deployments.tasks.deployment.tasks_templates.addon_provisioner.provision_dispatch')
def test_all_ai_templates_dry_run(mock_addon_provisioner, mock_subprocess_run, mock_smart_deploy):
    mock_addon_provisioner.return_value = ('test-uuid', 'postgres://...')
    from apps.deployments.tasks.deployment.tasks_templates import one_click_deploy_template_task

    # Mock subprocess.run to avoid docker manifest inspect calls
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    user = User.objects.create_user(username='test_user', password='password123')
    local_provider = CloudProvider.objects.create(
        name='Local Docker',
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True,
    )

    with open('apps/deployments/fixtures/templates.json', encoding='utf-8-sig') as f:
        templates = json.load(f)

    assert len(templates) > 0, "No templates found in fixtures!"

    for t in templates:
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
            # Should have created 3 companion services
            assert companions.count() == 3, f"Expected 3 companions for ai-router, got {companions.count()}"
            companion_names = [c.name for c in companions]
            assert any('llama3-1-7b' in name for name in companion_names)
            assert any('qwen2-5-0-5b' in name for name in companion_names)
            assert any('ollama-nomic-embed-text' in name for name in companion_names)

            # Check router env var
            env_var = EnvironmentVariable.objects.filter(service=service, key='AI_ROUTER_SELECTED_SERVICE_IDS').first()
            assert env_var is not None
            assert len(json.loads(env_var.value)) == 3

        # Clean up
        EnvironmentVariable.objects.filter(service__owner=user).delete()
        Service.objects.filter(owner=user).delete()
