from ._config import ConfigMixin
from ._deployment import DeploymentMixin
from ._health import HealthMixin
from ._failover import FailoverMixin
from ._helpers import HelpersMixin
from ._preflight import PreflightMixin


class ReplicationService(
    ConfigMixin,
    DeploymentMixin,
    HealthMixin,
    FailoverMixin,
    HelpersMixin,
    PreflightMixin,
):
    PATRONI_IMAGE = "ghcr.io/zalando/spilo-16:3.3-p3"
    ETCD_IMAGE = "quay.io/coreos/etcd:v3.5.9"
    HAPROXY_IMAGE = "haproxy:2.8"
    PATRONI_POSTGRES_PORT = 55432
