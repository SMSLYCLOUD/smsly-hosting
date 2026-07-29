from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import EnvironmentVariable, Project, Service
from apps.deployments.models.backup import ServiceSnapshot
from apps.deployments.services.snapshot_service import SnapshotService

User = get_user_model()


class SnapshotSystemTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(name="Test Project", owner=self.user)
        self.service = Service.objects.create(
            name='test-app',
            repository_url='https://github.com/test/app',
            owner=self.user,
            project=self.project,
            deploy_type='GIT',
            memory_mb=512,
            min_replicas=1,
            max_replicas=2,
            cpu_cores=0.5,
        )
        # Add environment variables
        self.ev1 = EnvironmentVariable.objects.create(
            service=self.service, key='APP_ENV', value='production'
        )
        self.ev_secret = EnvironmentVariable.objects.create(
            service=self.service, key='DB_PASSWORD', value='supersecret'
        )

    def test_snapshot_capture_service(self):
        # Capture a snapshot via service layer
        snapshot = SnapshotService.capture_snapshot(
            service_id=str(self.service.id),
            trigger='MANUAL',
            label='Initial Config',
            created_by=self.user,
        )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.label, 'Initial Config')
        self.assertEqual(snapshot.trigger, 'MANUAL')
        self.assertEqual(snapshot.service_id, self.service.id)

        # Check captured config data
        config = snapshot.config_data
        self.assertEqual(config['deploy_type'], 'GIT')
        self.assertEqual(config['memory_mb'], 512)
        self.assertEqual(config['cpu_cores'], '0.50')
        self.assertEqual(config['env_vars']['APP_ENV'], 'production')

        # Check credential masking
        self.assertEqual(config['env_vars']['DB_PASSWORD'], '****')

    def test_snapshot_restore_service(self):
        # Capture initial state
        snapshot = SnapshotService.capture_snapshot(
            service_id=str(self.service.id),
            trigger='MANUAL',
            label='Config to Restore',
            created_by=self.user,
        )

        # Change service config
        self.service.memory_mb = 1024
        self.service.deploy_type = 'DOCKER'
        self.service.save()

        # Update environment variable
        self.ev1.value = 'staging'
        self.ev1.save()

        # Restore snapshot
        result = SnapshotService.restore_snapshot(
            snapshot_id=str(snapshot.id),
            target_service_id=str(self.service.id),
        )

        changed_fields = [c['field'] for c in result['changes']]
        self.assertIn('memory_mb', changed_fields)
        self.assertIn('deploy_type', changed_fields)
        self.assertEqual(result['env_var_changes'], 1)  # APP_ENV

        # Verify values restored
        self.service.refresh_from_db()
        self.assertEqual(self.service.memory_mb, 512)
        self.assertEqual(self.service.deploy_type, 'GIT')

        self.ev1.refresh_from_db()
        self.assertEqual(self.ev1.value, 'production')

        # Verify masked env var was NOT overwritten
        self.ev_secret.refresh_from_db()
        self.assertEqual(self.ev_secret.value, 'supersecret')

    def test_snapshot_diff(self):
        # Snapshot A
        snap_a = SnapshotService.capture_snapshot(
            service_id=str(self.service.id),
            trigger='MANUAL',
            label='A',
            created_by=self.user,
        )

        # Change memory and add a key
        self.service.memory_mb = 1024
        self.service.save()
        EnvironmentVariable.objects.create(
            service=self.service, key='NEW_KEY', value='hello'
        )

        # Snapshot B
        snap_b = SnapshotService.capture_snapshot(
            service_id=str(self.service.id),
            trigger='MANUAL',
            label='B',
            created_by=self.user,
        )

        # Compute diff
        diff_res = SnapshotService.diff_snapshots(
            snapshot_a_id=str(snap_a.id),
            snapshot_b_id=str(snap_b.id),
        )

        diff = diff_res['diff']
        self.assertEqual(diff['total_changes'], 2)  # memory_mb and env_vars
        self.assertIn('memory_mb', diff['changed'])
        self.assertEqual(diff['changed']['memory_mb']['old'], 512)
        self.assertEqual(diff['changed']['memory_mb']['new'], 1024)

    def test_api_endpoints(self):
        # Create a snapshot via API
        url = reverse('service-snapshot-list', kwargs={'service_pk': self.service.id})
        data = {
            'service': self.service.id,
            'label': 'API Snapshot',
            'trigger': 'MANUAL',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ServiceSnapshot.objects.count(), 1)

        snapshot = ServiceSnapshot.objects.get()
        self.assertEqual(snapshot.label, 'API Snapshot')

        # List snapshots via API
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)

        # Restore snapshot via API
        restore_url = reverse('service-snapshot-restore', kwargs={'service_pk': self.service.id, 'pk': snapshot.id})
        restore_data = {
            'confirm': True,
            'redeploy': False,
        }
        response = self.client.post(restore_url, restore_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Second restore should result in 0 changes since fields are already normalized
        response2 = self.client.post(restore_url, restore_data, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data['config_changes'], 0)

