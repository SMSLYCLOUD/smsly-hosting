"""Tests for addon container start/stop actions (on-demand spin-up/parking)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.addons.views.crud import AddonViewSet


def _view(action):
    return AddonViewSet.as_view({'post': action})


class FakeContainer:
    def __init__(self, status):
        self.status = status
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=30):
        self.stopped = True


class StartStopTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = MagicMock()
        self.addon = MagicMock()
        self.addon.id = 'addon-1'
        self.addon.addon_type = 'POSTGRES'
        self.addon.service = MagicMock()

    def _request(self, action):
        req = self.factory.post(f'/addons/{self.addon.id}/{action}/')
        force_authenticate(req, user=self.user)
        return req

    @patch('apps.addons.views.crud.assert_can_write')
    @patch('apps.addons.views.crud.AddonViewSet.get_object')
    @patch('docker.from_env')
    def test_start_stopped(self, mock_docker, mock_get, mock_perm):
        mock_get.return_value = self.addon
        cont = FakeContainer('exited')
        mock_docker.return_value.containers.get.return_value = cont
        resp = _view('start_container')(self._request('start'), pk='addon-1')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(cont.started)
        self.assertEqual(resp.data['status'], 'started')

    @patch('apps.addons.views.crud.assert_can_write')
    @patch('apps.addons.views.crud.AddonViewSet.get_object')
    @patch('docker.from_env')
    def test_start_already_running(self, mock_docker, mock_get, mock_perm):
        mock_get.return_value = self.addon
        cont = FakeContainer('running')
        mock_docker.return_value.containers.get.return_value = cont
        resp = _view('start_container')(self._request('start'), pk='addon-1')
        self.assertEqual(resp.data['status'], 'already_running')
        self.assertFalse(cont.started)

    @patch('apps.addons.views.crud.assert_can_write')
    @patch('apps.addons.views.crud.AddonViewSet.get_object')
    @patch('docker.from_env')
    def test_stop_running(self, mock_docker, mock_get, mock_perm):
        mock_get.return_value = self.addon
        cont = FakeContainer('running')
        mock_docker.return_value.containers.get.return_value = cont
        resp = _view('stop_container')(self._request('stop'), pk='addon-1')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(cont.stopped)

    @patch('apps.addons.views.crud.assert_can_write')
    @patch('apps.addons.views.crud.AddonViewSet.get_object')
    @patch('docker.from_env')
    def test_stop_missing_container(self, mock_docker, mock_get, mock_perm):
        from docker.errors import NotFound
        mock_get.return_value = self.addon
        mock_docker.return_value.containers.get.side_effect = NotFound('nope')
        resp = _view('stop_container')(self._request('stop'), pk='addon-1')
        self.assertEqual(resp.status_code, 400)
