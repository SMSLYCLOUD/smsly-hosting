"""
Code Analysis — AI-powered codebase structure mapping.

Analyzes a service's git repository and generates a visual graph of:
- File/directory structure
- Import dependencies
- API routes
- Database models
- External integrations

Integrates with Intelligence AI providers for high-level architecture summary.
"""

import ast
import logging
import os
import re
import shutil
import tempfile
import uuid

from apps.deployments.models import Service
from celery import shared_task
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.next', '.nuxt', 'venv',
    '.venv', 'env', '.env', 'dist', 'build', '.cache', '.pytest_cache',
    'coverage', '.nyc_output', '.tox', 'eggs', '*.egg-info',
    'vendor', 'bower_components', '.idea', '.vscode',
}

SKIP_FILES = {
    '.DS_Store', 'Thumbs.db', 'package-lock.json', 'yarn.lock',
    'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock', 'composer.lock',
}

SOURCE_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java',
    '.rb', '.php', '.vue', '.svelte', '.css', '.scss', '.html',
    '.sql', '.graphql', '.proto', '.yaml', '.yml', '.toml', '.json',
    '.md', '.sh', '.dockerfile',
}

LANGUAGE_MAP = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
    '.tsx': 'typescript', '.jsx': 'javascript', '.go': 'go',
    '.rs': 'rust', '.java': 'java', '.rb': 'ruby', '.php': 'php',
    '.vue': 'vue', '.svelte': 'svelte', '.sql': 'sql',
    '.html': 'html', '.css': 'css', '.scss': 'scss',
    '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml',
    '.md': 'markdown', '.sh': 'shell', '.dockerfile': 'docker',
    '.toml': 'toml', '.graphql': 'graphql', '.proto': 'protobuf',
}

LANGUAGE_COLORS = {
    'python': '#3572A5', 'javascript': '#f1e05a', 'typescript': '#3178c6',
    'go': '#00ADD8', 'rust': '#dea584', 'java': '#b07219',
    'ruby': '#701516', 'php': '#4F5D95', 'vue': '#41b883',
    'svelte': '#ff3e00', 'html': '#e34c26', 'css': '#563d7c',
    'scss': '#c6538c', 'sql': '#e38c00', 'shell': '#89e051',
    'docker': '#384d54', 'json': '#292929', 'yaml': '#cb171e',
    'markdown': '#083fa1', 'toml': '#9c4221',
    'graphql': '#e10098', 'protobuf': '#63a375',
}

MAX_FILES = 500  # Safety cap
MAX_FILE_SIZE = 100_000  # 100KB per file
import contextlib

from apps.cloud.services.code_analyzer import MAX_TOTAL_BYTES

_AI_CODE_DISCLAIMER = (
    "NOTE: The following prompt contains structural metadata extracted from "
    "a customer's source repository (file paths, route labels, model names, "
    "and aggregate size statistics only). No file contents or secrets are "
    "included. Do not infer, fabricate, or repeat any code, credentials, or "
    "private identifiers. Respond with a high-level architecture summary "
    "only.\n\n"
)


# ─── File Analysis Helpers ───────────────────────────────────────────────────

def _extract_python_imports(content: str) -> list[str]:
    """Extract import targets from Python source."""
    imports = []
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
    except SyntaxError:
        # Fallback to regex for invalid Python
        for m in re.finditer(r'^(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
            imports.append(m.group(1))
    return imports


def _extract_js_imports(content: str) -> list[str]:
    """Extract import targets from JS/TS source."""
    imports = []
    # ES6 imports
    for m in re.finditer(r"(?:import|from)\s+['\"]([^'\"]+)['\"]", content):
        imports.append(m.group(1))
    # CommonJS requires
    for m in re.finditer(r"require\(['\"]([^'\"]+)['\"]\)", content):
        imports.append(m.group(1))
    return imports


def _extract_routes(content: str, lang: str) -> list[dict]:
    """Extract API route definitions."""
    routes = []

    if lang == 'python':
        # Django URL patterns
        for m in re.finditer(r"path\(['\"]([^'\"]+)['\"]", content):
            routes.append({'path': m.group(1), 'framework': 'django'})
        # Flask/FastAPI decorators
        for m in re.finditer(
            r"@\w+\.(get|post|put|delete|patch|route)\(['\"]([^'\"]+)['\"]",
            content, re.IGNORECASE,
        ):
            routes.append({'path': m.group(2), 'method': m.group(1).upper(), 'framework': 'flask/fastapi'})
        # DRF ViewSets
        for m in re.finditer(r"class\s+(\w+ViewSet|.*ViewSet)\s*\(", content):
            routes.append({'path': f'/{m.group(1)}/', 'framework': 'drf'})

    elif lang in ('javascript', 'typescript'):
        # Express/Next.js routes
        for m in re.finditer(
            r"\.(get|post|put|delete|patch)\(['\"]([^'\"]+)['\"]",
            content, re.IGNORECASE,
        ):
            routes.append({'path': m.group(2), 'method': m.group(1).upper(), 'framework': 'express'})
        # Next.js API routes are detected by file path

    return routes


def _extract_models(content: str, lang: str) -> list[str]:
    """Extract database model class names."""
    models = []
    if lang == 'python':
        for m in re.finditer(
            r"class\s+(\w+)\s*\(\s*(?:models\.Model|Base|db\.Model)",
            content,
        ):
            models.append(m.group(1))
    return models


def _detect_tech_stack(files: list[dict]) -> list[str]:
    """Detect technology stack from file patterns."""
    stack = set()
    filenames = {f['name'] for f in files}
    extensions = {f.get('language', '') for f in files}

    # Frameworks
    if 'manage.py' in filenames or 'wsgi.py' in filenames:
        stack.add('Django')
    if 'requirements.txt' in filenames or 'Pipfile' in filenames:
        stack.add('Python')
    if 'package.json' in filenames:
        stack.add('Node.js')
    if 'next.config.js' in filenames or 'next.config.mjs' in filenames or 'next.config.ts' in filenames:
        stack.add('Next.js')
    if 'nuxt.config.ts' in filenames or 'nuxt.config.js' in filenames:
        stack.add('Nuxt.js')
    if 'vite.config.ts' in filenames or 'vite.config.js' in filenames:
        stack.add('Vite')
    if 'Dockerfile' in filenames or 'docker-compose.yml' in filenames:
        stack.add('Docker')
    if 'Cargo.toml' in filenames:
        stack.add('Rust')
    if 'go.mod' in filenames:
        stack.add('Go')
    if 'Gemfile' in filenames:
        stack.add('Ruby')
    if 'composer.json' in filenames:
        stack.add('PHP')
    if 'pom.xml' in filenames or 'build.gradle' in filenames:
        stack.add('Java')
    if any('prisma' in f['path'] for f in files):
        stack.add('Prisma')
    if any('tailwind' in f['name'] for f in files):
        stack.add('Tailwind CSS')

    # Languages from extensions
    if 'python' in extensions:
        stack.add('Python')
    if 'typescript' in extensions:
        stack.add('TypeScript')
    if 'javascript' in extensions:
        stack.add('JavaScript')

    return sorted(stack)


# ─── Core Analysis ───────────────────────────────────────────────────────────

def analyze_codebase(repo_path: str) -> dict:
    """
    Walk a cloned repository and build a graph of its structure.

    Returns:
        {
            nodes: [...],
            edges: [...],
            tech_stack: [...],
            stats: { files, directories, lines, languages }
        }
    """
    nodes: list = []
    edges: list = []
    file_index: dict = {}  # path -> node_id
    dir_index: dict = {}   # dir_path -> node_id
    file_count = 0
    total_lines = 0
    total_bytes = 0
    lang_stats: dict = {}

    for root, dirs, filenames in os.walk(repo_path, topdown=True):
        # Filter out skipped directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]

        rel_root = os.path.relpath(root, repo_path)
        if rel_root == '.':
            rel_root = ''

        # Directory node
        if rel_root:
            dir_id = f"dir-{uuid.uuid4().hex[:8]}"
            dir_index[rel_root] = dir_id
            parent_dir = os.path.dirname(rel_root)
            nodes.append({
                'id': dir_id,
                'type': 'directory',
                'data': {
                    'name': os.path.basename(rel_root),
                    'label': os.path.basename(rel_root),
                    'path': rel_root,
                    'kind': 'DIRECTORY',
                },
            })
            # Edge to parent directory
            if parent_dir and parent_dir in dir_index:
                edges.append({
                    'id': f"e-{uuid.uuid4().hex[:8]}",
                    'source': dir_index[parent_dir],
                    'target': dir_id,
                    'type': 'CONTAINS',
                })

        for filename in filenames:
            if file_count >= MAX_FILES:
                break
            if filename in SKIP_FILES or filename.startswith('.'):
                continue

            ext = os.path.splitext(filename)[1].lower()
            if ext not in SOURCE_EXTENSIONS and filename not in ('Dockerfile', 'Makefile', 'Procfile'):
                continue

            rel_path = os.path.join(rel_root, filename) if rel_root else filename
            full_path = os.path.join(root, filename)

            # Read file
            try:
                file_size = os.path.getsize(full_path)
                if file_size > MAX_FILE_SIZE:
                    continue
                if total_bytes + file_size > MAX_TOTAL_BYTES:
                    raise ValidationError("Repository too large")
                with open(full_path, encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            file_count += 1
            total_bytes += file_size
            lines = content.count('\n') + 1
            total_lines += lines

            lang = LANGUAGE_MAP.get(ext, 'other')
            if filename == 'Dockerfile':
                lang = 'docker'
            lang_stats[lang] = lang_stats.get(lang, 0) + lines

            file_id = f"file-{uuid.uuid4().hex[:8]}"
            file_index[rel_path] = file_id

            # Extract imports
            imports = []
            if lang == 'python':
                imports = _extract_python_imports(content)
            elif lang in ('javascript', 'typescript'):
                imports = _extract_js_imports(content)

            # Extract routes
            routes = _extract_routes(content, lang)

            # Extract models
            models = _extract_models(content, lang)

            node_data = {
                'name': filename,
                'label': filename,
                'path': rel_path,
                'kind': 'FILE',
                'language': lang,
                'color': LANGUAGE_COLORS.get(lang, '#6b7280'),
                'lines': lines,
                'size': file_size,
                'imports': imports[:20],  # Cap for safety
                'routes': routes[:10],
                'models': models[:10],
            }

            nodes.append({
                'id': file_id,
                'type': 'file',
                'data': node_data,
            })

            # Edge to parent directory
            if rel_root and rel_root in dir_index:
                edges.append({
                    'id': f"e-{uuid.uuid4().hex[:8]}",
                    'source': dir_index[rel_root],
                    'target': file_id,
                    'type': 'CONTAINS',
                })

            # Route nodes
            for route in routes:
                route_id = f"route-{uuid.uuid4().hex[:8]}"
                nodes.append({
                    'id': route_id,
                    'type': 'route',
                    'data': {
                        'name': route['path'],
                        'label': f"{route.get('method', 'ANY')} {route['path']}",
                        'kind': 'ROUTE',
                        'framework': route.get('framework', ''),
                        'method': route.get('method', 'ANY'),
                    },
                })
                edges.append({
                    'id': f"e-{uuid.uuid4().hex[:8]}",
                    'source': file_id,
                    'target': route_id,
                    'type': 'DEFINES_ROUTE',
                })

            # Model nodes
            for model_name in models:
                model_id = f"model-{uuid.uuid4().hex[:8]}"
                nodes.append({
                    'id': model_id,
                    'type': 'model',
                    'data': {
                        'name': model_name,
                        'label': model_name,
                        'kind': 'MODEL',
                    },
                })
                edges.append({
                    'id': f"e-{uuid.uuid4().hex[:8]}",
                    'source': file_id,
                    'target': model_id,
                    'type': 'DEFINES_MODEL',
                })

    # ── Resolve import edges ────────────────────────────────────────────
    for rel_path, file_id in file_index.items():
        node = next((n for n in nodes if n['id'] == file_id), None)
        if not node:
            continue
        imports = node['data'].get('imports', [])
        for imp in imports:
            # Try to resolve to a known file
            imp_path = imp.replace('.', '/')
            candidates = [
                f"{imp_path}.py",
                f"{imp_path}/index.ts",
                f"{imp_path}/index.js",
                f"{imp_path}.ts",
                f"{imp_path}.tsx",
                f"{imp_path}.js",
                f"{imp_path}.jsx",
                imp_path,
            ]
            for candidate in candidates:
                if candidate in file_index:
                    edges.append({
                        'id': f"e-{uuid.uuid4().hex[:8]}",
                        'source': file_id,
                        'target': file_index[candidate],
                        'type': 'IMPORT',
                        'label': imp,
                    })
                    break

    # Build file list for tech stack detection
    file_list = [n['data'] for n in nodes if n['type'] == 'file']
    tech_stack = _detect_tech_stack(file_list)  # type: ignore[arg-type]

    return {
        'nodes': nodes,
        'edges': edges,
        'tech_stack': tech_stack,
        'stats': {
            'files': file_count,
            'directories': len(dir_index),
            'lines': total_lines,
            'languages': lang_stats,
        },
    }


# ─── AI Summary ──────────────────────────────────────────────────────────────

def _generate_ai_summary(analysis: dict) -> str:
    """Use AI to generate a high-level architecture summary."""
    try:
        from apps.intelligence.providers import ask_with_fallback

        file_nodes = [n for n in analysis['nodes'] if n['type'] == 'file']
        route_nodes = [n for n in analysis['nodes'] if n['type'] == 'route']
        model_nodes = [n for n in analysis['nodes'] if n['type'] == 'model']

        tech = ', '.join(analysis['tech_stack']) or 'Unknown'
        stats = analysis['stats']

        # Build a concise prompt
        prompt = _AI_CODE_DISCLAIMER + (
            f"Analyze this codebase structure and provide a brief architecture summary.\n\n"
            f"Tech Stack: {tech}\n"
            f"Stats: {stats['files']} files, {stats['lines']} lines, "
            f"{stats['directories']} directories\n"
            f"Languages: {', '.join(f'{k}: {v} lines' for k, v in sorted(stats['languages'].items(), key=lambda x: -x[1])[:5])}\n\n"
            f"Top files: {', '.join(n['data']['path'] for n in file_nodes[:15])}\n"
            f"API Routes: {', '.join(n['data']['label'] for n in route_nodes[:10]) or 'None detected'}\n"
            f"DB Models: {', '.join(n['data']['name'] for n in model_nodes[:10]) or 'None detected'}\n\n"
            f"Provide a 3-5 sentence architecture overview. What does this app do? "
            f"What patterns/frameworks does it use? What are the main components?"
        )

        response, _provider = ask_with_fallback(
            prompt=prompt,
            system_prompt=(
                "You are a senior software architect analyzing codebase structure. "
                "Be concise, technical, and actionable."
            ),
        )
        return response
    except Exception as exc:
        logger.warning("AI summary failed: %s", exc)
        return ""


# ─── Celery Task ─────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=1, soft_time_limit=300, time_limit=360)
def analyze_service_code_task(self, service_id: str, user_id: str):
    """
    Clone a service repo and analyze its codebase structure.
    Results are cached on the Service model (or a related store).
    """
    from apps.cloud.services.git_manager import GitManager
    from apps.deployments.utils import get_github_oauth_token_for_user
    from django.contrib.auth import get_user_model

    User = get_user_model()

    try:
        service = Service.objects.get(id=service_id)
        user = User.objects.get(id=user_id)
    except (Service.DoesNotExist, User.DoesNotExist):
        logger.error("Service or user not found: %s / %s", service_id, user_id)
        return None

    if not service.repository_url:
        return {'error': 'No repository URL configured'}

    # Get git token
    token = None
    with contextlib.suppress(Exception):
        token = get_github_oauth_token_for_user(user)

    # Clone to temp
    tmp_dir = tempfile.mkdtemp(prefix='code-analysis-')
    try:
        repo_dir = GitManager.clone_repo(
            repo_url=service.repository_url,
            branch=service.branch or 'main',
            destination=tmp_dir,
            token=token,
        )

        # Analyze
        analysis = analyze_codebase(repo_dir)

        # AI summary
        summary = _generate_ai_summary(analysis)
        analysis['summary'] = summary

        return analysis

    except Exception as exc:
        logger.exception("Code analysis failed for service %s", service_id)
        return {'error': str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── API ─────────────────────────────────────────────────────────────────────

class CodeAnalysisRequestSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()


class CodeAnalysisViewSet(viewsets.GenericViewSet):
    """API for AI-powered codebase analysis and visualization."""
    serializer_class = CodeAnalysisRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='analyze')
    def analyze(self, request):
        """
        Kick off async code analysis for a service.
        Returns a task_id to poll for results.
        """
        ser = CodeAnalysisRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        service_id = str(ser.validated_data['service_id'])

        # Verify ownership
        try:
            service = Service.objects.get(id=service_id, owner=request.user)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not service.repository_url:
            return Response(
                {'error': 'Service has no repository URL configured'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = analyze_service_code_task.delay(service_id, str(request.user.id))

        return Response({
            'task_id': task.id,
            'status': 'analyzing',
            'service': service.name,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['get'], url_path='result/(?P<task_id>[^/.]+)')
    def result(self, request, task_id=None):
        """
        Poll for analysis results by task_id.
        """
        from celery.result import AsyncResult

        result = AsyncResult(task_id)

        if result.state == 'PENDING':
            return Response({'status': 'pending'})
        elif result.state == 'STARTED':
            return Response({'status': 'analyzing'})
        elif result.state == 'SUCCESS':
            data = result.result
            if isinstance(data, dict) and 'error' in data:
                return Response({
                    'status': 'failed',
                    'error': data['error'],
                })
            return Response({
                'status': 'complete',
                'data': data,
            })
        elif result.state == 'FAILURE':
            return Response({
                'status': 'failed',
                'error': str(result.result),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            return Response({'status': result.state.lower()})
