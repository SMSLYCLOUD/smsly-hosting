"""
Automatic Codebase Scanner for SMSLY Hosting AI.

Scans a cloned repository to build comprehensive AI context:
- Config files (.env, .env.example, docker-compose, Dockerfile, etc.)
- Package manifests (package.json, requirements.txt, Cargo.toml, etc.)
- Build configs (nixpacks.toml, Procfile, next.config.js, etc.)
- Directory structure and root-level analysis
- Environment variable detection (what the app expects)

Returns a structured context dict that AI providers use for intelligent
pre-deploy analysis and auto-fix decisions.
"""
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File categories to scan
# ---------------------------------------------------------------------------

# Config files that define how the app runs
CONFIG_FILES = {
    'Dockerfile', 'Dockerfile.dev', 'Dockerfile.prod',
    'docker-compose.yml', 'docker-compose.yaml',
    'docker-compose.prod.yml', 'docker-compose.dev.yml',
    'docker-compose.override.yml',
    '.dockerignore',
    'nixpacks.toml', 'nixpacks.json',
    'Procfile', 'heroku.yml',
    'fly.toml', 'render.yaml', 'railway.toml',
    'app.yaml', 'app.json',
    'vercel.json', 'netlify.toml',
    'nginx.conf', 'Caddyfile',
    '.htaccess',
}

# Package manifests that define dependencies
PACKAGE_FILES = {
    'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'requirements.txt', 'Pipfile', 'Pipfile.lock', 'pyproject.toml', 'setup.py', 'setup.cfg',
    'Cargo.toml', 'Cargo.lock',
    'go.mod', 'go.sum',
    'Gemfile', 'Gemfile.lock',
    'composer.json', 'composer.lock',
    'pom.xml', 'build.gradle', 'build.gradle.kts',
    'mix.exs',
}

# Build/runtime config files
BUILD_FILES = {
    'next.config.js', 'next.config.mjs', 'next.config.ts',
    'vite.config.ts', 'vite.config.js',
    'webpack.config.js',
    'tsconfig.json', 'tsconfig.base.json',
    'babel.config.js', '.babelrc',
    'tailwind.config.js', 'tailwind.config.ts',
    'postcss.config.js', 'postcss.config.mjs',
    'jest.config.js', 'vitest.config.ts',
    'manage.py', 'wsgi.py', 'asgi.py',
    'settings.py', 'config.py',
    'Makefile', 'justfile',
    'Rakefile',
}

# Environment files — critical for detecting required env vars
ENV_FILES = {
    '.env', '.env.example', '.env.sample', '.env.template',
    '.env.local', '.env.development', '.env.production',
    '.env.staging', '.env.test',
    'env.example', 'env.sample',
}

# Directories to always skip during scanning
SKIP_DIRS = {
    '.git', 'node_modules', 'venv', '.venv', 'env',
    '__pycache__', '.next', '.nuxt', 'dist', 'build',
    'target', 'vendor', '.cargo', '.tox',
    'coverage', '.pytest_cache', '.mypy_cache',
    '.turbo', '.vercel', '.netlify',
}

# Max bytes to read per file (prevent reading massive lock files)
MAX_FILE_READ = 5000
# Max bytes for lock files (just read enough to confirm format)
MAX_LOCK_READ = 500


class RepoScanner:
    """Scans a cloned repository and builds AI-ready context."""

    def __init__(self, source_dir: str):
        self.source_dir = source_dir

    def scan(self) -> dict[str, Any]:
        """
        Full scan of the repository.

        Returns a structured dict with:
        - stack: detected tech stack
        - configs: contents of config files
        - env_vars: detected env vars with context
        - structure: directory tree summary
        - issues: potential deployment problems detected
        """
        self._detected_prefixes: set[str] = set()
        env_context = self._detect_env_vars_with_context()
        result = {
            'stack': self._detect_stack(),
            'configs': self._read_config_files(),
            'env_vars': list(env_context.keys()),
            'env_vars_context': env_context,
            'env_prefixes': list(self._detected_prefixes),
            'structure': self._directory_summary(),
            'issues': [],
        }

        # Detect potential issues
        result['issues'] = self._detect_issues(result)

        return result

    def build_ai_context(self) -> str:
        """
        Scan and format everything into a single AI-ready prompt context.
        This is what gets sent to the AI providers.
        """
        scan = self.scan()

        sections = []

        # Stack detection
        sections.append(f"## Detected Stack\n{scan['stack']}")

        # Directory structure
        sections.append(f"## Project Structure\n```\n{scan['structure']}\n```")

        # Config files
        if scan['configs']:
            config_text = []
            for path, content in scan['configs'].items():
                config_text.append(f"### {path}\n```\n{content}\n```")
            sections.append("## Configuration Files\n" + "\n\n".join(config_text))

        # Expected env vars with context
        if scan['env_vars_context']:
            env_lines = ["## Expected Environment Variables (with Context)"]
            for var, contexts in sorted(scan['env_vars_context'].items()):
                env_lines.append(f"### `{var}`")
                for ctx in contexts[:3]: # Limit to 3 snippets per var
                    env_lines.append(f"- Context: `{ctx}`")
            sections.append("\n".join(env_lines))

        # Detected issues
        if scan['issues']:
            issue_text = "\n".join(f"- ⚠️ {issue}" for issue in scan['issues'])
            sections.append(f"## Potential Issues Detected\n{issue_text}")

        return "\n\n".join(sections)

    # -----------------------------------------------------------------------
    # Stack Detection
    # -----------------------------------------------------------------------

    def _detect_stack(self) -> str:
        """Detect the tech stack from file markers."""
        markers = []

        checks = [
            ('package.json', 'Node.js'),
            ('next.config.js', 'Next.js'),
            ('next.config.mjs', 'Next.js'),
            ('next.config.ts', 'Next.js'),
            ('vite.config.ts', 'Vite'),
            ('vite.config.js', 'Vite'),
            ('requirements.txt', 'Python'),
            ('Pipfile', 'Python (Pipenv)'),
            ('pyproject.toml', 'Python'),
            ('manage.py', 'Django'),
            ('Cargo.toml', 'Rust'),
            ('go.mod', 'Go'),
            ('Gemfile', 'Ruby'),
            ('composer.json', 'PHP'),
            ('pom.xml', 'Java (Maven)'),
            ('build.gradle', 'Java/Kotlin (Gradle)'),
            ('mix.exs', 'Elixir'),
            ('Dockerfile', 'Docker'),
            ('docker-compose.yml', 'Docker Compose'),
        ]

        for filename, stack in checks:
            # Check root level
            if os.path.exists(os.path.join(self.source_dir, filename)):
                markers.append(stack)
            # Check one level deep (monorepo support)
            for subdir in self._get_subdirs():
                if os.path.exists(os.path.join(self.source_dir, subdir, filename)):
                    markers.append(f"{stack} (in {subdir}/)")

        if not markers:
            return "Unknown — no recognized stack markers found"

        return ", ".join(dict.fromkeys(markers))  # Deduplicate preserving order

    def _safe_read(self, filepath: str, max_read: int = 50000) -> str:
        """Read a file safely, stripping NUL bytes for Postgres compatibility."""
        try:
            with open(filepath, errors='ignore', encoding='utf-8') as fh:
                content = fh.read(max_read)
                sanitized = content.replace('\x00', '')
                if len(content) == max_read:
                    sanitized += "\n... (truncated)"
                return sanitized
        except Exception as e:
            logger.debug("Failed to read %s: %s", filepath, e)
            return ""

    # -----------------------------------------------------------------------
    # Config File Reading
    # -----------------------------------------------------------------------

    def _read_config_files(self) -> dict[str, str]:
        """Read all config, package, build, and env files."""
        configs = {}
        all_targets = CONFIG_FILES | PACKAGE_FILES | BUILD_FILES | ENV_FILES

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            # No depth limit for config scanning

            for f in files:
                if f in all_targets or f.endswith(('.env', '.toml', '.yaml', '.yml')):
                    filepath = os.path.join(root, f)
                    rel_path = os.path.relpath(filepath, self.source_dir)

                    # Determine read limit
                    is_lock = f.endswith(('.lock', '-lock.yaml'))
                    max_read = MAX_LOCK_READ if is_lock else MAX_FILE_READ

                    try:
                        content = self._safe_read(filepath, max_read)
                        configs[rel_path] = content
                    except Exception as e: # pylint: disable=broad-exception-caught
                        configs[rel_path] = f"(unreadable: {e})"

        return configs

    # -----------------------------------------------------------------------
    # Environment Variable Detection
    # -----------------------------------------------------------------------

    def _detect_env_vars_with_context(self) -> dict[str, list[str]]:
        # pylint: disable=too-many-locals, too-many-branches
        """
        Detect all environment variables the app expects, along with code context.
        Scans .env files, code files, and config files for patterns.
        Covers 50+ frameworks and languages.
        """
        env_vars: dict[str, list[str]] = {}

        def add_var(name: str, context: str):
            name = name.strip()
            if not name or not re.match(r'^[A-Z_][A-Z0-9_]*$', name):
                return
            if name not in env_vars:
                env_vars[name] = []
            if context and context not in env_vars[name]:
                env_vars[name].append(context)

        # 1. Parse .env files for variable names
        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]


            for f in files:
                if f in ENV_FILES or f.startswith('.env'):
                    filepath = os.path.join(root, f)
                    try:
                        content = self._safe_read(filepath)
                        for line in content.splitlines():
                            line = line.strip()
                            if line and not line.startswith('#') and '=' in line:
                                key = line.split('=', 1)[0].strip()
                                key = re.sub(r'^export\s+', '', key)
                                add_var(key, f"Found in {f}")
                    except Exception: # pylint: disable=broad-exception-caught
                        pass

        # 2. Scan code files for env var patterns across 50+ frameworks
        code_patterns = [
            # ── Python ──
            re.compile(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'os\.environ\[["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'os\.getenv\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'environ\[["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'config\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'env\(["\']([A-Z_][A-Z0-9_]*)["\']\)?'),
            re.compile(r'env\.[a-z]+\(["\']([A-Z_][A-Z0-9_]*)["\']\)?'),
            re.compile(r'^\s*([A-Z_][A-Z0-9_]{3,})\s*:\s*(?:str|int|bool|float|list|dict|AnyHttpUrl|PostgresDsn|RedisDsn|SecretStr|SecretBytes|EmailStr|AnyUrl|Field)'), # Pydantic settings
            re.compile(r'Field\(.*?(?:env|alias)=["\']([A-Z_][A-Z0-9_]*)["\']'),

            # ── JavaScript / TypeScript ──
            re.compile(r'process\.env\.([A-Z_][A-Z0-9_]*)'),
            re.compile(r'process\.env\[["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'import\.meta\.env\.([A-Z_][A-Z0-9_]*)'),
            re.compile(r'config\.get\(["\']([A-Z_][A-Z0-9_]*)["\']\)'), # Node config package
            re.compile(r'configService\.get(?:OrThrow)?\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'env: ["\']([A-Z_][A-Z0-9_]*)["\']'), # Next.js / Vite configs
            re.compile(r'RuntimeConfig.*?([A-Z_][A-Z0-9_]*)'),
            re.compile(r'(?:const|let|var)\s*\{\s*[^}]*\b([A-Z_][A-Z0-9_]*)\b[^}]*\}\s*=\s*process\.env'),

            # ── Go ──
            re.compile(r'os\.Getenv\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'viper\.(?:Get|GetString|GetInt|GetBool)\(["\']([A-Z_][A-Z0-9_]*)["\']'),

            # ── Rust ──
            re.compile(r'std::env::var\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'env::var\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'dotenv!\(["\']([A-Z_][A-Z0-9_]*)["\']'),

            # ── PHP ──
            re.compile(r'getenv\(["\']([A-Z_][A-Z0-9_]*)["\']\)'),
            re.compile(r'\$_(?:ENV|SERVER)\[["\']([A-Z_][A-Z0-9_]*)["\']\]'),

            # ── Ruby ──
            re.compile(r'ENV\[["\']([A-Z_][A-Z0-9_]*)["\']\]'),
            re.compile(r'ENV\.fetch\(["\']([A-Z_][A-Z0-9_]*)["\']'),

            # ── Java / Kotlin ──
            re.compile(r'System\.getenv\(["\']([A-Z_][A-Z0-9_]*)["\']\)'),

            # ── C# / .NET ──
            re.compile(r'Environment\.GetEnvironmentVariable\(["\']([A-Z_][A-Z0-9_]*)["\']\)'),
        ]

        code_extensions = {'.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.rb', '.php', '.java', '.kt', '.cs', '.yaml', '.yml', '.toml', '.json'}

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]


            for f in files:
                _, ext = os.path.splitext(f)
                if ext not in code_extensions and f not in CONFIG_FILES:
                    continue

                filepath = os.path.join(root, f)
                try:
                    # Increase read limit for aggressive scanning
                    content = self._safe_read(filepath, 100000)
                    lines = content.splitlines()
                    for i, line in enumerate(lines):
                        for pattern in code_patterns:
                            for match in pattern.finditer(line):
                                try:
                                    var_name = match.group(1)
                                    context = line.strip()
                                    # Capture 1 line above and below for better context
                                    prev_line = lines[i-1].strip() if i > 0 else ""
                                    next_line = lines[i+1].strip() if i < len(lines)-1 else ""
                                    full_ctx = f"{prev_line}\n{context}\n{next_line}".strip()
                                    add_var(var_name, full_ctx)
                                except (IndexError, AttributeError):
                                    continue
                except Exception: # pylint: disable=broad-exception-caught
                    pass

        # 3. Post-processing: detect pydantic env_prefix and lowercase snake_case fields
        pydantic_prefix_pat = re.compile(r'env_prefix\s*=\s*["\']([A-Z_][A-Z0-9_]*)["\']')
        pydantic_field_pat = re.compile(r'^\s+([a-z_][a-z0-9_]+)\s*:\s*(?:str|int|bool|float|SecretStr|SecretBytes|AnyHttpUrl|PostgresDsn|RedisDsn|AnyUrl)\s*(?:[=,\n]|$)')

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if not f.endswith('.py'):
                    continue
                filepath = os.path.join(root, f)
                try:
                    content = self._safe_read(filepath, MAX_FILE_READ)
                    prefix = ""
                    pm = pydantic_prefix_pat.search(content)
                    if pm:
                        prefix = pm.group(1)
                        self._detected_prefixes.add(prefix)
                    for m in pydantic_field_pat.finditer(content):
                        env_var = (prefix + m.group(1).upper()) if prefix else m.group(1).upper()
                        ctx_line = m.group(0).strip()
                        add_var(env_var, f"Found in {f} (pydantic field): {ctx_line}")
                except Exception:
                    pass

        # 4. Scan docker-compose files for ${VAR} interpolation
        compose_pattern = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)(?::?[-?+])?[^}]*\}')
        docker_env_pattern = re.compile(r'ENV\s+([A-Z_][A-Z0-9_]*)(.*)')
        docker_arg_pattern = re.compile(r'ARG\s+([A-Z_][A-Z0-9_]*)(.*)')

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if not (f.startswith('docker-compose') or f == 'Dockerfile' or f.endswith(('.yml', '.yaml'))):
                    continue
                filepath = os.path.join(root, f)
                try:
                    content = self._safe_read(filepath, MAX_FILE_READ)
                    # Scan for compose interpolations
                    for match in compose_pattern.finditer(content):
                        add_var(match.group(1), f"Found in {f} (interpolation)")

                    # Scan for Docker ENV/ARG
                    for match in docker_env_pattern.finditer(content):
                        val = match.group(2).strip()
                        add_var(match.group(1), f"Found in {f} (ENV: {val})")
                    for match in docker_arg_pattern.finditer(content):
                        val = match.group(2).strip()
                        add_var(match.group(1), f"Found in {f} (ARG: {val})")

                    # Scan for environment blocks in docker-compose YAML files
                    if f.startswith('docker-compose') or f in ('compose.yml', 'compose.yaml'):
                        import yaml
                        try:
                            compose_data = yaml.safe_load(content)
                            if compose_data and isinstance(compose_data, dict):
                                services = compose_data.get('services', {})
                                if isinstance(services, dict):
                                    for svc_name, svc_def in services.items():
                                        if isinstance(svc_def, dict):
                                            env = svc_def.get('environment')
                                            if isinstance(env, dict):
                                                for k in env:
                                                    add_var(str(k), f"Found in {f} ({svc_name} environment block)")
                                            elif isinstance(env, list):
                                                for item in env:
                                                    if isinstance(item, str) and '=' in item:
                                                        k = item.split('=', 1)[0].strip()
                                                        add_var(k, f"Found in {f} ({svc_name} environment block)")
                                                    elif isinstance(item, str):
                                                        add_var(item, f"Found in {f} ({svc_name} environment block pass-through)")
                        except Exception:
                            pass
                except Exception:
                    pass

        return env_vars

    def _detect_env_vars(self) -> list[str]:
        """Legacy wrapper for flat list return."""
        return sorted(self._detect_env_vars_with_context().keys())

    # -----------------------------------------------------------------------
    # Directory Structure
    # -----------------------------------------------------------------------

    def _directory_summary(self, max_depth: int = 2) -> str:
        """Generate a tree-like directory summary."""
        lines: list[str] = []
        self._build_tree(self.source_dir, "", max_depth, 0, lines)
        return "\n".join(lines[:100])  # Cap at 100 lines

    def _build_tree(self, path: str, prefix: str, max_depth: int,
                    current_depth: int, lines: list[str]):
        # pylint: disable=too-many-arguments, too-many-positional-arguments
        if current_depth > max_depth:
            return

        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return

        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e)) and e not in SKIP_DIRS]
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]

        for f in files:
            lines.append(f"{prefix}{f}")

        for i, d in enumerate(dirs):
            is_last = i == len(dirs) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{d}/")
            extension = "    " if is_last else "│   "
            self._build_tree(
                os.path.join(path, d),
                prefix + extension,
                max_depth, current_depth + 1, lines
            )

    def _get_subdirs(self) -> list[str]:
        """Get immediate subdirectories (for monorepo detection)."""
        try:
            return [
                d for d in os.listdir(self.source_dir)
                if os.path.isdir(os.path.join(self.source_dir, d))
                and d not in SKIP_DIRS
            ]
        except Exception: # pylint: disable=broad-exception-caught
            return []

    # -----------------------------------------------------------------------
    # Issue Detection
    # -----------------------------------------------------------------------

    def _detect_issues(self, scan: dict) -> list[str]:
        """Detect potential deployment issues from scan results."""
        issues = []
        configs = scan.get('configs', {})
        stack = scan.get('stack', '')

        # Monorepo without root marker
        subdirs = self._get_subdirs()
        has_root_package = 'package.json' in configs or 'requirements.txt' in configs
        has_subdir_packages = any(
            f"{d}/package.json" in configs or f"{d}/requirements.txt" in configs
            for d in subdirs
        )
        if has_subdir_packages and not has_root_package:
            issues.append(
                "MONOREPO DETECTED: No root-level package manifest found. "
                "Nixpacks will fail unless `root_directory` is set to the correct subdirectory "
                f"(e.g., {subdirs[0]}/)."
            )

        # No Dockerfile and no recognized stack
        if 'Dockerfile' not in configs and 'Unknown' in stack:
            issues.append(
                "NO BUILD STRATEGY: No Dockerfile and no recognized language stack. "
                "Nixpacks cannot auto-detect the build process."
            )

        # Missing start command
        if 'Procfile' not in configs and 'Dockerfile' not in configs:
            # Check if package.json has a start script
            pkg = configs.get('package.json', '')
            if 'package.json' in configs and '"start"' not in pkg:
                issues.append(
                    "MISSING START SCRIPT: package.json has no 'start' script. "
                    "Nixpacks may not know how to launch the application."
                )

        # .env required but not all vars configured
        env_vars = scan.get('env_vars', [])
        critical_vars = {'DATABASE_URL', 'SECRET_KEY', 'DB_URL', 'REDIS_URL', 'API_KEY'}
        missing_critical = [v for v in env_vars if v in critical_vars]
        if missing_critical:
            issues.append(
                f"CRITICAL ENV VARS DETECTED: {', '.join(missing_critical)} — "
                "make sure these are set in the service environment variables."
            )

        # Docker Compose without Dockerfile (compose-based project)
        if any('docker-compose' in k for k in configs) and 'Dockerfile' not in configs:
            if 'in' not in stack.lower():  # Not a subdir detection
                issues.append(
                    "DOCKER COMPOSE PROJECT: This project uses docker-compose but "
                    "has no Dockerfile. Set `root_directory` to the service subdirectory "
                    "that contains the buildable app."
                )

        return issues
