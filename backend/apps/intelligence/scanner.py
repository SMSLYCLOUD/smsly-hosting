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
import os
import re
import logging
from typing import Dict, List, Any

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

    def scan(self) -> Dict[str, Any]:
        """
        Full scan of the repository.

        Returns a structured dict with:
        - stack: detected tech stack
        - configs: contents of config files
        - env_vars: expected environment variables
        - structure: directory tree summary
        - issues: potential deployment problems detected
        """
        result = {
            'stack': self._detect_stack(),
            'configs': self._read_config_files(),
            'env_vars': self._detect_env_vars(),
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

        # Expected env vars
        if scan['env_vars']:
            env_text = "\n".join(f"- `{var}`" for var in sorted(scan['env_vars']))
            sections.append(f"## Expected Environment Variables\n{env_text}")

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

    # -----------------------------------------------------------------------
    # Config File Reading
    # -----------------------------------------------------------------------

    def _read_config_files(self) -> Dict[str, str]:
        """Read all config, package, build, and env files."""
        configs = {}
        all_targets = CONFIG_FILES | PACKAGE_FILES | BUILD_FILES | ENV_FILES

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            # Limit depth to 3 levels
            depth = root.replace(self.source_dir, '').count(os.sep)
            if depth > 3:
                dirs.clear()
                continue

            for f in files:
                if f in all_targets or f.endswith(('.env', '.toml', '.yaml', '.yml')):
                    filepath = os.path.join(root, f)
                    rel_path = os.path.relpath(filepath, self.source_dir)

                    # Determine read limit
                    is_lock = f.endswith(('.lock', '-lock.yaml'))
                    max_read = MAX_LOCK_READ if is_lock else MAX_FILE_READ

                    try:
                        with open(filepath, 'r', errors='ignore', encoding='utf-8') as fh:
                            content = fh.read(max_read)
                            if len(content) == max_read:
                                content += "\n... (truncated)"
                            configs[rel_path] = content
                    except Exception as e: # pylint: disable=broad-exception-caught
                        configs[rel_path] = f"(unreadable: {e})"

        return configs

    # -----------------------------------------------------------------------
    # Environment Variable Detection
    # -----------------------------------------------------------------------

    def _detect_env_vars(self) -> List[str]:
        # pylint: disable=too-many-locals, too-many-branches
        """
        Detect all environment variables the app expects.
        Scans .env files, code files, and config files for patterns.
        """
        env_vars = set()

        # 1. Parse .env files for variable names
        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            depth = root.replace(self.source_dir, '').count(os.sep)
            if depth > 2:
                dirs.clear()
                continue

            for f in files:
                if f in ENV_FILES or f.startswith('.env'):
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath, 'r', errors='ignore', encoding='utf-8') as fh:
                            for line in fh:
                                line = line.strip()
                                if line and not line.startswith('#') and '=' in line:
                                    # pylint: disable=superfluous-parens
                                    key = line.split('=', 1)[0].strip()
                                    if key and re.match(r'^[A-Z_][A-Z0-9_]*$', key):
                                        env_vars.add(key)
                    except Exception: # pylint: disable=broad-exception-caught
                        pass

        # 2. Scan code files for os.environ / process.env patterns
        code_patterns = [
            re.compile(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'os\.environ\[["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'os\.getenv\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'process\.env\.([A-Z_][A-Z0-9_]*)'),
            re.compile(r'env\(["\']([A-Z_][A-Z0-9_]*)["\']'),
            re.compile(r'std::env::var\(["\']([A-Z_][A-Z0-9_]*)["\']'),
        ]

        code_extensions = {'.py', '.js', '.ts', '.jsx', '.tsx', '.rs', '.go', '.rb'}

        for root, dirs, files in os.walk(self.source_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            depth = root.replace(self.source_dir, '').count(os.sep)
            if depth > 4:
                dirs.clear()
                continue

            for f in files:
                _, ext = os.path.splitext(f)
                if ext not in code_extensions:
                    continue

                filepath = os.path.join(root, f)
                try:
                    with open(filepath, 'r', errors='ignore', encoding='utf-8') as fh:
                        content = fh.read(50000)  # Read first 50KB of code files
                        for pattern in code_patterns:
                            for match in pattern.finditer(content):
                                env_vars.add(match.group(1))
                except Exception: # pylint: disable=broad-exception-caught
                    pass

        return sorted(env_vars)

    # -----------------------------------------------------------------------
    # Directory Structure
    # -----------------------------------------------------------------------

    def _directory_summary(self, max_depth: int = 2) -> str:
        """Generate a tree-like directory summary."""
        lines = []
        self._build_tree(self.source_dir, "", max_depth, 0, lines)
        return "\n".join(lines[:100])  # Cap at 100 lines

    def _build_tree(self, path: str, prefix: str, max_depth: int,
                    current_depth: int, lines: List[str]):
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

    def _get_subdirs(self) -> List[str]:
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

    def _detect_issues(self, scan: Dict) -> List[str]:
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
