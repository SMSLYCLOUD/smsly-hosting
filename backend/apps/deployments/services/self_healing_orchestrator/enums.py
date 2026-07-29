from enum import Enum


class FailureType(Enum):
    CONTAINER_CRASHED = "container_crashed"
    CONTAINER_RESTARTING = "container_restarting"
    OUT_OF_MEMORY = "out_of_memory"
    DISK_FULL = "disk_full"
    NETWORK_UNREACHABLE = "network_unreachable"
    DOCKER_DAEMON_DOWN = "docker_daemon_down"
    BUILDX_BROKEN = "buildx_broken"
    IMAGE_PULL_FAILED = "image_pull_failed"
    PORT_CONFLICT = "port_conflict"
    CONFIG_ERROR = "config_error"
    DEPLOYMENT_TIMEOUT = "deployment_timeout"
    UNKNOWN = "unknown"


class RecoveryAction(Enum):
    RESTART_CONTAINER = "restart_container"
    RESTART_STACK = "restart_stack"
    RESTART_DOCKER_DAEMON = "restart_docker_daemon"
    REBUILD_CONTAINER = "rebuild_container"
    REPAIR_BUILDX = "repair_buildx"
    PRUNE_IMAGES = "prune_images"
    PRUNE_VOLUMES = "prune_volumes"
    FIX_NETWORK = "fix_network"
    FIX_PERMISSIONS = "fix_permissions"
    INCREASE_RESOURCES = "increase_resources"
    ROLLBACK = "rollback"
    REPROVISION = "reprovision"
    ESCALATE_TO_AI = "escalate_to_ai"
    NONE = "none"
