"""
AI-Powered Repository Analysis View

Analyzes Git repositories to detect framework, runtime, and provide intelligent
deployment suggestions. Features real GitHub API integration for accurate detection.
"""
import base64
import json
import logging
import re

import requests
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Framework detection patterns
FRAMEWORK_PATTERNS: dict = {
    # JavaScript/Node.js frameworks
    'nextjs': {'files': ['next.config.js', 'next.config.mjs', 'next.config.ts'], 'deps': ['next'], 'port': 3000},
    'react': {'files': [], 'deps': ['react', 'react-dom'], 'port': 3000},
    'vue': {'files': ['vue.config.js', 'vite.config.ts'], 'deps': ['vue'], 'port': 5173},
    'nuxt': {'files': ['nuxt.config.js', 'nuxt.config.ts'], 'deps': ['nuxt'], 'port': 3000},
    'express': {'files': [], 'deps': ['express'], 'port': 3000},
    'nestjs': {'files': ['nest-cli.json'], 'deps': ['@nestjs/core'], 'port': 3000},
    'hono': {'files': [], 'deps': ['hono'], 'port': 3000},
    'svelte': {'files': ['svelte.config.js'], 'deps': ['svelte'], 'port': 5173},

    # Python frameworks
    'django': {'files': ['manage.py', 'settings.py'], 'deps': ['django', 'Django'], 'port': 8000},
    'fastapi': {'files': [], 'deps': ['fastapi'], 'port': 8000},
    'flask': {'files': [], 'deps': ['flask', 'Flask'], 'port': 5000},

    # Go
    'go': {'files': ['go.mod', 'main.go'], 'deps': [], 'port': 8080},

    # Rust
    'rust': {'files': ['Cargo.toml'], 'deps': [], 'port': 8080},

    # Ruby
    'rails': {'files': ['Gemfile', 'config.ru'], 'deps': ['rails'], 'port': 3000},

    # PHP
    'laravel': {'files': ['artisan', 'composer.json'], 'deps': ['laravel/framework'], 'port': 8000},
    'wordpress': {'files': ['wp-config.php', 'wp-content'], 'deps': [], 'port': 80},
}

# Build command suggestions
BUILD_COMMANDS = {
    'nextjs': {'build': 'npm run build', 'start': 'npm start'},
    'react': {'build': 'npm run build', 'start': 'npx serve -s build'},
    'vue': {'build': 'npm run build', 'start': 'npx serve -s dist'},
    'express': {'build': 'npm install', 'start': 'npm start'},
    'nestjs': {'build': 'npm run build', 'start': 'npm run start:prod'},
    'django': {'build': 'pip install -r requirements.txt', 'start': 'gunicorn config.wsgi:application'},
    'fastapi': {'build': 'pip install -r requirements.txt', 'start': 'uvicorn main:app --host 0.0.0.0'},
    'flask': {'build': 'pip install -r requirements.txt', 'start': 'gunicorn app:app'},
    'go': {'build': 'go build -o main .', 'start': './main'},
    'rust': {'build': 'cargo build --release', 'start': './target/release/app'},
}

# Environment variable templates — rich objects
ENV_VAR_TEMPLATES = {
    'nextjs': ['NEXT_PUBLIC_API_URL', 'NEXTAUTH_SECRET', 'DATABASE_URL'],
    'django': ['SECRET_KEY', 'DEBUG', 'DATABASE_URL', 'ALLOWED_HOSTS'],
    'fastapi': ['DATABASE_URL', 'SECRET_KEY', 'API_KEY'],
    'flask': ['SECRET_KEY', 'DATABASE_URL', 'FLASK_ENV'],
    'express': ['PORT', 'DATABASE_URL', 'JWT_SECRET'],
    'nestjs': ['PORT', 'DATABASE_URL', 'JWT_SECRET'],
    'rails': ['RAILS_ENV', 'SECRET_KEY_BASE', 'DATABASE_URL'],
    'laravel': ['APP_KEY', 'DB_CONNECTION', 'DB_HOST', 'DB_DATABASE'],
}

# Hints for common env vars — used to enrich analysis results
ENV_VAR_HINTS = {
    'SECRET_KEY': {'hint': 'Random 50+ char string for cryptographic signing', 'is_secret': True, 'required': True, 'generate': True},
    'NEXTAUTH_SECRET': {'hint': 'Random string for NextAuth session encryption', 'is_secret': True, 'required': True, 'generate': True},
    'JWT_SECRET': {'hint': 'Random string for JWT token signing', 'is_secret': True, 'required': True, 'generate': True},
    'SECRET_KEY_BASE': {'hint': 'Random hex string (rails secret)', 'is_secret': True, 'required': True, 'generate': True},
    'APP_KEY': {'hint': 'base64:... Laravel app key', 'is_secret': True, 'required': True, 'generate': True},
    'DATABASE_URL': {'hint': 'postgres://user:pass@host:5432/dbname', 'is_secret': True, 'required': True},
    'API_KEY': {'hint': 'API key from your provider', 'is_secret': True, 'required': True, 'user_required': True},
    'OPENAI_API_KEY': {'hint': 'sk-... from platform.openai.com', 'is_secret': True, 'required': True, 'user_required': True},
    'GEMINI_API_KEY': {'hint': 'From aistudio.google.com', 'is_secret': True, 'required': True, 'user_required': True},
    'ANTHROPIC_API_KEY': {'hint': 'sk-ant-... from console.anthropic.com', 'is_secret': True, 'required': True, 'user_required': True},
    'JULES_API_KEY': {'hint': 'From your Jules console/provider', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_SECRET_KEY': {'hint': 'sk_live_... or sk_test_... from Stripe dashboard', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_PUBLISHABLE_KEY': {'hint': 'pk_live_... or pk_test_...', 'is_secret': False, 'required': True, 'user_required': True},
    'NEXT_PUBLIC_API_URL': {'hint': 'https://api.example.com', 'is_secret': False, 'required': False},
    'DEBUG': {'hint': 'False for production', 'default': 'False', 'required': False},
    'FLASK_ENV': {'hint': 'production or development', 'default': 'production', 'required': False},
    'RAILS_ENV': {'hint': 'production or development', 'default': 'production', 'required': False},
    'PORT': {'hint': 'Integer, e.g. 8000', 'required': False},
    'ALLOWED_HOSTS': {'hint': 'Comma-separated domains or *', 'default': '*', 'required': False},
    'AI_PROVIDER': {'hint': 'openai | grok | gemini | claude | jules | auto', 'required': True, 'user_required': True},
    'QDRANT_PORT': {'hint': 'Integer, usually 6333', 'required': True},
    'QDRANT_HOST': {'hint': 'Qdrant server hostname', 'required': True, 'user_required': True},
    'REDIS_URL': {'hint': 'redis://localhost:6379/0', 'is_secret': False, 'required': False},
    'SENTRY_DSN': {'hint': 'https://...@sentry.io/...', 'is_secret': True, 'required': False, 'user_required': True},
}


class RepoAnalysisSchemaSerializer(serializers.Serializer):
    """Schema placeholder for repo analysis API."""


class RepoAnalysisView(GenericAPIView):
    """
    AI-powered repository analysis endpoint.

    POST /api/v1/analyze-repo/
    {
        "repo_url": "https://github.com/user/repo"
    }

    Returns detected framework, port, build commands, and suggestions.
    """
    serializer_class = RepoAnalysisSchemaSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        repo_url = request.data.get('repo_url')
        if not repo_url:
            return Response({"detail": "repo_url required"},
                            status=status.HTTP_400_BAD_REQUEST)

        # SECURITY: Validate repo URL format to prevent SSRF
        if not re.match(
                r'^https?://(github\.com|gitlab\.com|bitbucket\.org)/', repo_url):
            return Response(
                {"detail": "Only GitHub, GitLab, and Bitbucket repositories are supported."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Extract owner/repo from URL
            match = re.match(
                r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$',
                repo_url)
            if match:
                owner, repo = match.groups()
                analysis = self._analyze_github_repo(owner, repo, request.user)
            else:
                # Fallback for non-GitHub or complex URLs
                analysis = self._fallback_analysis(repo_url)

            logger.info(
                f"Repo analysis for {repo_url}: {analysis.get('detected_framework')}")
            return Response(analysis)

        except Exception as e:
            logger.error(f"Repo analysis error: {e}")
            return Response(
                {"detail": "Failed to analyze repository. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _analyze_github_repo(self, owner: str, repo: str, user) -> dict:
        """Analyze a GitHub repository using the API."""
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        token = self._get_github_access_token(user)

        try:
            # Fetch root directory
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'SMSLY-Hosting-Analyzer'
            }
            if token:
                headers['Authorization'] = f"token {token}"

            response = requests.get(api_url, timeout=10, headers=headers)

            if response.status_code == 404:
                return self._build_response(
                    'unknown', confidence=0.3, error="Repository not found or private")

            if response.status_code != 200:
                return self._fallback_analysis(
                    f"https://github.com/{owner}/{repo}")

            files = response.json()
            file_names = [f['name'] for f in files if isinstance(f, dict)]

            # Detect framework from files
            framework, confidence = self._detect_framework(file_names, owner, repo, token)

            return self._build_response(framework, confidence, file_names,
                                        owner=owner, repo=repo, token=token)

        except requests.Timeout:
            return self._fallback_analysis(
                f"https://github.com/{owner}/{repo}")
        except Exception as e:
            logger.error(f"GitHub API error: {e}")
            return self._fallback_analysis(
                f"https://github.com/{owner}/{repo}")

    def _detect_framework(self, files: list, owner: str, repo: str, token: str | None) -> tuple:
        """Detect framework from file list and package.json."""
        detected = 'unknown'
        confidence = 0.3

        # Check for known files
        for framework, patterns in FRAMEWORK_PATTERNS.items():
            for pattern_file in patterns['files']:
                if pattern_file in files:
                    detected = framework
                    confidence = 0.9
                    break
            if confidence > 0.7:
                break

        # If still unknown, check package.json/requirements.txt
        if detected == 'unknown' or confidence < 0.8:
            if 'package.json' in files:
                detected, confidence = self._check_package_json(owner, repo, token)
            elif 'requirements.txt' in files:
                detected, confidence = self._check_requirements(owner, repo, token)
            elif 'go.mod' in files:
                detected, confidence = 'go', 0.95
            elif 'Cargo.toml' in files:
                detected, confidence = 'rust', 0.95

        return detected, confidence

    def _check_package_json(self, owner: str, repo: str, token: str | None) -> tuple:
        """Check package.json for framework dependencies."""
        try:
            content = self._fetch_github_file(owner, repo, "package.json", token, ref="main")
            if content is None:
                content = self._fetch_github_file(owner, repo, "package.json", token, ref="master")

            if content is not None:
                pkg = json.loads(content)
                all_deps = {**pkg.get('dependencies', {}),
                            **pkg.get('devDependencies', {})}

                # Check for frameworks by dependency
                for framework, patterns in FRAMEWORK_PATTERNS.items():
                    for dep in patterns.get('deps', []):
                        if dep in all_deps:
                            return framework, 0.95

                # Default to generic node
                return 'express', 0.6

        except Exception as e:
            logger.debug(f"Could not parse package.json: {e}")

        return 'node', 0.5

    def _check_requirements(self, owner: str, repo: str, token: str | None) -> tuple:
        """Check requirements.txt for Python framework."""
        try:
            content = self._fetch_github_file(owner, repo, "requirements.txt", token, ref="main")
            if content is None:
                content = self._fetch_github_file(owner, repo, "requirements.txt", token, ref="master")

            if content is not None:
                content = content.lower()
                if 'django' in content:
                    return 'django', 0.95
                elif 'fastapi' in content:
                    return 'fastapi', 0.95
                elif 'flask' in content:
                    return 'flask', 0.95

        except Exception as e:
            logger.debug(f"Could not parse requirements.txt: {e}")

        return 'python', 0.5

    def _get_github_access_token(self, user) -> str | None:
        """Return the linked GitHub OAuth access token for the user, if available."""
        try:
            from allauth.socialaccount.models import SocialAccount, SocialToken
        except Exception:
            return None

        account = (
            SocialAccount.objects.filter(user=user, provider="github")
            .order_by("-id")
            .first()
        )
        if not account:
            return None

        token = (
            SocialToken.objects.filter(account=account)
            .order_by("-id")
            .first()
        )
        return getattr(token, "token", None) or None

    def _fetch_github_file(self, owner: str, repo: str, path: str, token: str | None, ref: str) -> str | None:
        """Fetch a file via the GitHub Contents API (works for private repos with token)."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SMSLY-Hosting-Analyzer",
        }
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            response = requests.get(url, timeout=10, headers=headers, params={"ref": ref})
        except requests.Timeout:
            return None
        except Exception:
            return None

        if response.status_code != 200:
            return None

        data = response.json()
        if not isinstance(data, dict):
            return None
        if data.get("encoding") != "base64" or "content" not in data:
            return None

        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            return None

    def _scan_env_example(self, owner: str, repo: str, token: str | None,
                          files: list) -> list:
        """Scan .env.example / .env.sample for app-specific env vars."""
        env_files = ['.env.example', '.env.sample', '.env.template', '.env']
        found_vars = []

        for env_file in env_files:
            if files and env_file not in files:
                continue
            for ref in ('main', 'master'):
                content = self._fetch_github_file(owner, repo, env_file, token, ref=ref)
                if content:
                    for line in content.splitlines():
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key = line.split('=', 1)[0].strip()
                            if key and key.isidentifier():
                                found_vars.append(key)
                    break  # found the file, stop trying refs
        return found_vars

    def _scan_config_patterns(self, owner: str, repo: str, token: str | None,
                               files: list) -> list:
        """Scan config.py / settings.py for os.environ / os.getenv patterns."""
        import re as _re
        config_files = ['config.py', 'settings.py', 'app/config.py',
                        'src/config.py', 'backend/config.py']
        found_vars = []

        for cf in config_files:
            if files and cf.split('/')[-1] not in files:
                continue
            for ref in ('main', 'master'):
                content = self._fetch_github_file(owner, repo, cf, token, ref=ref)
                if content:
                    # Match os.environ['VAR'], os.environ.get('VAR'), os.getenv('VAR')
                    patterns = _re.findall(
                        r"os\.(?:environ\[?['\"]|environ\.get\(['\"]|getenv\(['\"])([A-Z_][A-Z0-9_]*)",
                        content
                    )
                    found_vars.extend(patterns)
                    # Also match pydantic Field(..., env='VAR') patterns
                    pydantic_vars = _re.findall(
                        r"env=['\"]([A-Z_][A-Z0-9_]*)",
                        content
                    )
                    found_vars.extend(pydantic_vars)
                    break
        return list(set(found_vars))

    def _enrich_env_vars(self, var_keys: list, port: int | None = None) -> list:
        """Convert plain key names into rich env var objects with hints."""
        import secrets
        seen = set()
        result = []

        for key in var_keys:
            if key in seen:
                continue
            seen.add(key)

            hints = ENV_VAR_HINTS.get(key, {})
            obj = {
                'key': key,
                'hint': hints.get('hint', ''),
                'required': hints.get('required', True),
                'is_secret': hints.get('is_secret', 'key' in key.lower() or 'secret' in key.lower() or 'password' in key.lower()),
                'user_required': hints.get('user_required', False),
            }

            # Auto-generate values for generatable secrets
            if hints.get('generate'):
                obj['default'] = secrets.token_urlsafe(48)
                obj['user_required'] = False
            elif 'default' in hints:
                obj['default'] = hints['default']

            # PORT gets the detected port as default
            if key == 'PORT' and port:
                obj['default'] = str(port)

            result.append(obj)

        return result

    def _build_response(self, framework: str, confidence: float,
                        files: list | None = None, error: str | None = None,
                        owner: str | None = None, repo: str | None = None,
                        token: str | None = None) -> dict:
        """Build the analysis response with enriched env vars."""
        port = FRAMEWORK_PATTERNS.get(framework, {}).get('port', 8080)
        commands = BUILD_COMMANDS.get(framework, {'build': '', 'start': ''})
        template_vars = list(ENV_VAR_TEMPLATES.get(framework, []))

        # Scan .env.example and config patterns for app-specific vars
        extra_vars = []
        if owner and repo:
            extra_vars += self._scan_env_example(owner, repo, token, files or [])
            extra_vars += self._scan_config_patterns(owner, repo, token, files or [])

        # Merge: template vars first, then extras (deduplicated)
        all_var_keys = template_vars[:]
        seen = {v.upper() for v in template_vars}
        for v in extra_vars:
            if v.upper() not in seen:
                all_var_keys.append(v)
                seen.add(v.upper())

        # Enrich with hints
        enriched_vars = self._enrich_env_vars(all_var_keys, int(port) if port is not None else None)

        # Detect if Dockerfile exists
        has_dockerfile = files and 'Dockerfile' in files if files else False

        # Resource recommendations based on framework
        if framework in ['nextjs', 'nuxt', 'react', 'vue']:
            resources = {'cpu': '0.5', 'memory': '512Mi',
                         'recommendation': 'Good for static/SSR sites'}
        elif framework in ['django', 'rails', 'laravel']:
            resources = {
                'cpu': '1',
                'memory': '1Gi',
                'recommendation': 'Full-stack framework needs more resources'}
        elif framework in ['fastapi', 'express', 'hono']:
            resources = {'cpu': '0.25', 'memory': '256Mi',
                         'recommendation': 'Lightweight API framework'}
        else:
            resources = {'cpu': '0.5', 'memory': '512Mi',
                         'recommendation': 'Standard allocation'}

        response = {
            'detected_framework': framework,
            'confidence': confidence,
            'suggested_port': port,
            'build_command': commands['build'],
            'start_command': commands['start'],
            'has_dockerfile': has_dockerfile,
            'suggested_env_vars': enriched_vars,
            'resource_recommendation': resources,
            'detected_files': files[:10] if files else [],
        }

        if error:
            response['warning'] = error

        return response

    def _fallback_analysis(self, repo_url: str) -> dict:
        """Fallback heuristic analysis based on URL."""
        url_lower = repo_url.lower()

        if 'django' in url_lower:
            return self._build_response('django', 0.6)
        elif 'next' in url_lower or 'react' in url_lower:
            return self._build_response('nextjs', 0.5)
        elif 'flask' in url_lower:
            return self._build_response('flask', 0.6)
        elif 'fastapi' in url_lower:
            return self._build_response('fastapi', 0.6)
        elif 'express' in url_lower or 'node' in url_lower:
            return self._build_response('express', 0.5)
        return self._build_response('unknown', 0.3)

class CodeIntelligenceView(GenericAPIView):
    """
    POST /api/v1/cloud/ecosystem/deep_scan/
    Triggers the deep codebase analysis and verification task.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ai_provider = request.data.get('ai_provider', 'auto')
        repos_data = request.data.get('repos_data') or []
        deploy_plan = request.data.get('deploy_plan', {})

        if not isinstance(repos_data, list) or not deploy_plan:
            return Response({"detail": "repos_data and deploy_plan are required."}, status=400)

        from rest_framework.exceptions import PermissionDenied

        from apps.deployments.models import Service
        from apps.deployments.tasks.ecosystem import _repository_url

        for repo in repos_data:
            if not isinstance(repo, dict):
                continue
            owner_id = repo.get('owner_id')
            if owner_id and owner_id != request.user.id:
                raise PermissionDenied(
                    f"Repo {repo.get('id') or repo.get('repo', '<unknown>')} is not owned by you."
                )
            if owner_id is None:
                repo_id = repo.get('id') or repo.get('repo_id')
                repo_url = repo.get('repo') or repo.get('html_url') or repo.get('url')
                if repo_id:
                    if not Service.objects.filter(id=repo_id, owner=request.user).exists():
                        logger.debug("Deep scan: repo_id %s not found for user %s; proceeding anyway", repo_id, request.user.id)
                elif repo_url:
                    normalized = _repository_url(repo_url)
                    if not Service.objects.filter(owner=request.user, repository_url=normalized).exists():
                        logger.debug("Deep scan: repo %s (%s) not found in user's services; proceeding anyway", repo_url, normalized)

        from apps.deployments.tasks.ai.tasks_code_intelligence import deep_scan_and_verify_task

        task = deep_scan_and_verify_task.delay(
            user_id=request.user.id,
            repos_data=repos_data,
            deploy_plan=deploy_plan,
            ai_provider=ai_provider
        )

        return Response({"task_id": task.id})

class DeepScanTaskStatusView(GenericAPIView):
    """
    GET /api/v1/cloud/ecosystem/deep_scan/status/?task_id=...
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        task_id = request.query_params.get('task_id')
        if not task_id:
            return Response({"error": "task_id required"}, status=400)

        import json

        from celery.result import AsyncResult
        task_result = AsyncResult(task_id)

        result_value = None
        if task_result.ready():
            result_value = task_result.result
            if isinstance(result_value, Exception):
                exception_type = result_value.__class__.__name__
                message = str(result_value) or exception_type
                if exception_type == "SoftTimeLimitExceeded":
                    message = "Background task timed out before it could finish. Retry with a smaller batch or try again later."
                result_value = {
                    'error': message,
                    'exception_type': exception_type,
                }
            else:
                try:
                    json.dumps(result_value)
                except TypeError:
                    result_value = str(result_value)

        response_data = {
            'task_id': task_id,
            'status': task_result.status,
            'result': result_value,
        }

        # Include custom progress state if available
        if task_result.status == 'PROGRESS' and isinstance(task_result.info, dict):
            response_data['result'] = task_result.info

        # Handle failure nicely
        if task_result.status == 'FAILURE':
            if isinstance(result_value, dict) and 'error' in result_value:
                response_data['error'] = result_value['error']
            else:
                response_data['error'] = str(task_result.result)

        return Response(response_data)
