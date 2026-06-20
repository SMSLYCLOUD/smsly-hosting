from abc import ABC, abstractmethod
from typing import Any


class FrameworkAdapter(ABC):
    """
    Interface for framework-specific detection and deployment commands.
    """
    @abstractmethod
    def detect(self, project_path: str) -> bool:
        pass

    @abstractmethod
    def get_install_command(self) -> str:
        pass

    @abstractmethod
    def get_build_command(self) -> str:
        pass

    @abstractmethod
    def get_start_command(self) -> str:
        pass

    @abstractmethod
    def get_migration_plan_command(self) -> str:
        pass

    @abstractmethod
    def get_migration_apply_command(self) -> str:
        pass

    @abstractmethod
    def get_migration_check_command(self) -> str:
        pass

    @abstractmethod
    def get_test_command(self) -> str:
        pass

    @abstractmethod
    def get_health_check_config(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def inspect_migration_files(self, project_path: str) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def classify_migration_risk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        pass

class DatabaseSnapshotManager(ABC):
    """
    Interface for managing database clones/snapshots.
    """
    @abstractmethod
    def create_clone(self, source_db_name: str, clone_db_name: str) -> bool:
        pass

    @abstractmethod
    def destroy_clone(self, clone_db_name: str) -> bool:
        pass

    @abstractmethod
    def get_clone_url(self, clone_db_name: str) -> str:
        pass
