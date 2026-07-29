from .mixins import (
    CoreMixin,
    DnsMixin,
    DockerMixin,
    ExecMixin,
    RestoreMixin,
    TransferMixin,
    UploadMixin,
)


class ServerTransferService(
    CoreMixin,
    ExecMixin,
    RestoreMixin,
    TransferMixin,
    UploadMixin,
    DnsMixin,
    DockerMixin,
):
    pass
