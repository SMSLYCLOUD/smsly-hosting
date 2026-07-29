# pylint: disable=invalid-name
"""
Regression tests for Issue 44 (VolumeViewSet.get_queryset returns
empty when service_pk is missing).

Before the fix, when the ``service_pk`` URL kwarg was missing,
``get_queryset`` returned ``Volume.objects.none()`` — meaning
no user could ever list their volumes from an un-nested URL.
After the fix, the same code path returns the caller's own
volumes, scoped by service ownership.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.storage import Volume
from apps.deployments.views.storage import VolumeViewSet

User = get_user_model()


class VolumeListNoServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='vol-list-user', password='123',
        )
        self.other = User.objects.create_user(
            username='vol-list-other', password='123',
        )
        self.provider = CloudProvider.objects.create(
            name='vol-list-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.user_service = Service.objects.create(
            name='user-svc',
            owner=self.user,
            provider=self.provider,
        )
        self.other_service = Service.objects.create(
            name='other-svc',
            owner=self.other,
            provider=self.provider,
        )
        self.user_volume = Volume.objects.create(
            service=self.user_service,
            name='user-vol',
            mount_path='/data',
        )
        self.other_volume = Volume.objects.create(
            service=self.other_service,
            name='other-vol',
            mount_path='/data',
        )
        self.factory = RequestFactory()

    def _get_queryset_without_service_pk(self, user):
        request = self.factory.get('/api/v1/volumes/')
        request.user = user
        view = VolumeViewSet()
        view.request = request
        view.kwargs = {}
        return view.get_queryset()

    def test_unnested_list_returns_users_own_volumes(self):
        qs = self._get_queryset_without_service_pk(self.user)
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.user_volume.id, ids)
        self.assertNotIn(self.other_volume.id, ids)

    def test_unnested_list_excludes_other_users_volumes(self):
        qs = self._get_queryset_without_service_pk(self.user)
        for volume in qs:
            self.assertEqual(volume.service.owner_id, self.user.id)

    def test_unnested_list_is_per_user(self):
        qs_a = self._get_queryset_without_service_pk(self.user)
        qs_b = self._get_queryset_without_service_pk(self.other)
        self.assertIn(self.user_volume.id, set(qs_a.values_list('id', flat=True)))
        self.assertIn(self.other_volume.id, set(qs_b.values_list('id', flat=True)))
