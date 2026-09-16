import ast
import os
from typing import Any

from apps.deployments.models.safedeploy import MigrationValidation

from .command_executor import CommandExecutor


class DjangoAdapter:
    def __init__(self, python_bin: str = "python"):
        self.executor = CommandExecutor()
        self.python_bin = python_bin

    def detect(self, project_path: str) -> bool:
        if not project_path:
            return False
        return os.path.exists(os.path.join(project_path, 'manage.py'))

    def run_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run(f"{self.python_bin} manage.py check", cwd, env)

    def run_makemigrations_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run(f"{self.python_bin} manage.py makemigrations --check --dry-run", cwd, env)

    def run_showmigrations(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run(f"{self.python_bin} manage.py showmigrations --plan", cwd, env)

    def run_migrate(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run(f"{self.python_bin} manage.py migrate --noinput", cwd, env)

    def inspect_migration_files(self, project_path: str) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        if not os.path.exists(project_path):
            return operations
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d == 'migrations']
            if not dirs and os.path.basename(root) != 'migrations':
                continue
            for file in files:
                if not (file.endswith('.py') and file != '__init__.py'):
                    continue
                filepath = os.path.join(root, file)
                try:
                    with open(filepath) as f:
                        source = f.read()
                    tree = ast.parse(source, filename=filepath)
                    operations.extend(self._extract_operations_from_ast(tree, filepath))
                except (SyntaxError, ValueError):
                    pass
        return operations

    def _extract_operations_from_ast(self, tree: ast.AST, filepath: str) -> list[dict[str, Any]]:
        op_names = {
            'DeleteModel', 'RemoveField', 'RunPython', 'RunSQL',
            'AlterField', 'AddField', 'RenameField', 'RenameModel',
            'CreateModel', 'AddIndex', 'RemoveIndex',
        }
        operations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                op_name = node.func.attr
                if op_name in op_names:
                    entry: dict[str, Any] = {'type': op_name, 'file': os.path.basename(filepath)}
                    if op_name == 'RunPython':
                        entry['no_reverse'] = self._runpython_has_no_reverse(node)
                    if op_name == 'AddField':
                        # Expand/contract needs to know whether the new column
                        # is nullable or has a default. A NOT NULL column with
                        # no default blocks on a populated table AND breaks old
                        # readers, so it is contract-unsafe.
                        entry['nullable'] = self._addfield_is_nullable(node)
                        entry['has_default'] = self._addfield_has_default(node)
                    operations.append(entry)
        return operations

    @staticmethod
    def _addfield_is_nullable(call_node: ast.Call) -> bool:
        """Best-effort detection of ``null=True`` on an AddField call.

        The AST shape varies (``field=models.CharField(null=True)`` nested
        inside the AddField ``field=`` kwarg), so walk the whole call subtree
        for any ``null=True`` keyword. Unknown → False (conservative).
        """
        for sub in ast.walk(call_node):
            if isinstance(sub, ast.keyword) and sub.arg == 'null':
                if isinstance(sub.value, ast.Constant):
                    return bool(sub.value.value)
        return False

    @staticmethod
    def _addfield_has_default(call_node: ast.Call) -> bool:
        """Best-effort detection of a ``default=`` on an AddField call."""
        for sub in ast.walk(call_node):
            if isinstance(sub, ast.keyword) and sub.arg == 'default':
                return True
        return False

    @staticmethod
    def _runpython_has_no_reverse(call_node: ast.Call) -> bool:
        for kw in call_node.keywords:
            if kw.arg == 'reverse_code':
                return bool(isinstance(kw.value, ast.Constant) and kw.value.value is None)
        return True

    def classify_migration_risk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        score_map = {
            'DeleteModel': 100, 'RunSQL': 100,
            'RemoveField': 50, 'RunPython': 50,
            'AlterField': 25, 'RemoveIndex': 25,
            'RenameField': 25, 'RenameModel': 25,
            'AddField': 0, 'AddIndex': 0, 'CreateModel': 0,
        }
        risk_score = 0
        has_critical = has_high = has_medium = False
        reasons = []
        # Expand/contract tracking: while old + new code run concurrently
        # (canary weighted split on a shared DB), any contract op breaks the
        # old version. Expand ops are safe; contract ops block canary.
        has_expand_op = False
        contract_ops: list[str] = []
        for op in operations:
            op_type = str(op.get('type') or "")
            score = score_map.get(op_type, 0)
            if op_type == 'AddField' and not (op.get('nullable') or op.get('has_default')):
                # NOT NULL with no default: blocks on populated tables and
                # old readers can't satisfy it. Treat as contract-unsafe.
                score = 50
            risk_score += score
            if op_type in ('DeleteModel', 'RunSQL'):
                has_critical = True
            elif op_type in ('RemoveField', 'RunPython'):
                has_high = True
            elif op_type in ('AlterField', 'RemoveIndex', 'RenameField', 'RenameModel'):
                has_medium = True
            if score >= 25:
                reasons.append(f"Contains {op_type}.")
            if op_type == 'RunPython' and op.get('no_reverse'):
                reasons.append("RunPython has no reverse — migration cannot be undone.")
            if op_type in ('AddField', 'CreateModel', 'AddIndex'):
                if op_type == 'AddField' and not (op.get('nullable') or op.get('has_default')):
                    contract_ops.append(f"AddField (NOT NULL, no default) in {op.get('file', '?')}")
                else:
                    has_expand_op = True
            elif op_type in (
                'DeleteModel', 'RemoveField', 'RenameField', 'RenameModel',
                'AlterField', 'RunSQL', 'RunPython', 'RemoveIndex',
            ):
                contract_ops.append(f"{op_type} in {op.get('file', '?')}")
        risk_score = min(risk_score, 100)
        if has_critical:
            level = MigrationValidation.RiskLevel.CRITICAL
        elif has_high:
            level = MigrationValidation.RiskLevel.HIGH
        elif has_medium:
            level = MigrationValidation.RiskLevel.MEDIUM
        else:
            level = MigrationValidation.RiskLevel.LOW
        if contract_ops:
            expand_phase = 'MIXED' if has_expand_op else 'CONTRACT'
            is_expand_safe = False
        elif has_expand_op:
            expand_phase = 'EXPAND'
            is_expand_safe = True
        else:
            expand_phase = 'NONE'
            is_expand_safe = True
        canary_block_reasons: list[str] = []
        for item in contract_ops:
            canary_block_reasons.append(
                f"Contract op blocks shared-DB canary: {item} — "
                "ship it in a later release after the canary is at 100%."
            )
        if expand_phase == 'MIXED':
            canary_block_reasons.append(
                "Release mixes expand + contract ops — split into two "
                "releases (expand first, contract after cutover)."
            )
        canary_allowed = not canary_block_reasons
        requires_expand_contract = bool(contract_ops)
        return {
            'risk_level': level,
            'risk_score': risk_score,
            'reasons': reasons,
            'requires_manual_approval': level in (MigrationValidation.RiskLevel.HIGH, MigrationValidation.RiskLevel.CRITICAL),
            'requires_backup': level != MigrationValidation.RiskLevel.LOW,
            'can_auto_deploy': level == MigrationValidation.RiskLevel.LOW,
            'auto_deploy_policy': (
                MigrationValidation.AutoDeployPolicy.LOW_RISK_ONLY
                if level == MigrationValidation.RiskLevel.LOW
                else MigrationValidation.AutoDeployPolicy.NEVER
            ),
            'summary': f"Migration risk is {level} (Score: {risk_score})",
            # Expand/contract (shared-DB canary safety). All new keys;
            # existing consumers are unaffected.
            'is_expand_safe': is_expand_safe,
            'expand_contract_phase': expand_phase,
            'canary_allowed': canary_allowed,
            'canary_block_reasons': canary_block_reasons,
            'requires_expand_contract': requires_expand_contract,
        }
