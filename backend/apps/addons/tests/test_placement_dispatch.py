"""provision_dispatch honors Service.addon_placement (AUTO/MASTER/NODE)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.addons.services.addon_provisioner import AddonProvisioner


def _addon(placement, remote=True):
    server = SimpleNamespace(
        name='node-1', is_primary=not remote, is_lite_agent=False)
    return SimpleNamespace(
        service=SimpleNamespace(server=server, addon_placement=placement))


class PlacementDispatchTests(TestCase):
    def _route(self, placement, remote=True):
        prov = AddonProvisioner()
        with patch.object(prov, 'provision', return_value=('c1', 'u1')) as p_local, \
                patch.object(prov, 'provision_remote', return_value=('c2', 'u2')) as p_remote:
            cid, _url = prov.provision_dispatch(_addon(placement, remote))
        if p_remote.called:
            return 'remote', cid
        return 'local', cid

    def test_auto_remote_goes_remote(self):
        self.assertEqual(self._route('AUTO', True)[0], 'remote')

    def test_auto_local_goes_local(self):
        self.assertEqual(self._route('AUTO', False)[0], 'local')

    def test_master_pins_remote_service_to_master(self):
        self.assertEqual(self._route('MASTER', True)[0], 'local')

    def test_node_pins_remote_service_to_node(self):
        self.assertEqual(self._route('NODE', True)[0], 'remote')

    def test_node_on_local_service_stays_local(self):
        self.assertEqual(self._route('NODE', False)[0], 'local')

    def test_unknown_value_falls_back_to_auto(self):
        self.assertEqual(self._route('BOGUS', True)[0], 'remote')
        self.assertEqual(self._route('', True)[0], 'remote')
