import os
from typing import Dict, Any, List
from .command_executor import CommandExecutor
from apps.deployments.models_safedeploy import MigrationValidation

class DjangoAdapter:
    def __init__(self):
        self.executor = CommandExecutor()

    def detect(self, project_path: str) -> bool:
        return os.path.exists(os.path.join(project_path, 'manage.py'))

    def run_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py check", cwd, env)

    def run_makemigrations_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py makemigrations --check --dry-run", cwd, env)

    def run_showmigrations(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py showmigrations --plan", cwd, env)

    def run_migrate(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py migrate --noinput", cwd, env)

    def inspect_migration_files(self, project_path: str) -> List[Dict[str, Any]]:
        operations = []
        if not os.path.exists(project_path): return operations
        for root, _, files in os.walk(project_path):
            if 'migrations' in root:
                for file in files:
                    if file.endswith('.py') and file != '__init__.py':
                        filepath = os.path.join(root, file)
                        try:
                            with open(filepath, 'r') as f:
                                content = f.read()
                                if 'migrations.RemoveField' in content: operations.append({'type': 'RemoveField', 'file': file})
                                if 'migrations.DeleteModel' in content: operations.append({'type': 'DeleteModel', 'file': file})
                                if 'migrations.RunPython' in content: operations.append({'type': 'RunPython', 'file': file})
                                if 'migrations.RunSQL' in content: operations.append({'type': 'RunSQL', 'file': file})
                                if 'migrations.AlterField' in content: operations.append({'type': 'AlterField', 'file': file})
                                if 'migrations.AddField' in content: operations.append({'type': 'AddField', 'file': file})
                        except Exception:
                            pass
        return operations

    def classify_migration_risk(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        risk_score = 0
        reasons = []
        has_critical = False
        has_high = False
        has_medium = False

        for op in operations:
            op_type = op.get('type')
            if op_type in ['DeleteModel', 'RunSQL']:
                has_critical = True
                risk_score += 100
                reasons.append(f"Contains {op_type}.")
            elif op_type in ['RemoveField', 'RunPython']:
                has_high = True
                risk_score += 50
                reasons.append(f"Contains {op_type}.")
            elif op_type in ['AlterField']:
                has_medium = True
                risk_score += 20

        if risk_score > 100: risk_score = 100

        risk_level = MigrationValidation.RiskLevel.LOW
        if has_critical: risk_level = MigrationValidation.RiskLevel.CRITICAL
        elif has_high: risk_level = MigrationValidation.RiskLevel.HIGH
        elif has_medium: risk_level = MigrationValidation.RiskLevel.MEDIUM

        return {
            'risk_level': risk_level,
            'risk_score': risk_score,
            'reasons': reasons,
            'requires_manual_approval': risk_level in [MigrationValidation.RiskLevel.HIGH, MigrationValidation.RiskLevel.CRITICAL],
            'requires_backup': risk_level != MigrationValidation.RiskLevel.LOW,
            'can_auto_deploy': risk_level == MigrationValidation.RiskLevel.LOW,
            'summary': f"Migration risk is {risk_level} (Score: {risk_score})"
        }
