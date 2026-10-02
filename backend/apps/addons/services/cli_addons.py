"""AI coding-agent CLI addons (internal-only dedicated containers).

Each type provisions a container based on ``node:22-bookworm-slim``
with the vendor CLI installed idempotently at container start and a
tiny no-dependency Node status listener on the addon port (the generic
provision path requires a TCP health target).

No ``dashboard_port`` is set on purpose — these stay internal-only and
the expose guard refuses public URLs for them. Configuration (API key
+ model selection) is managed through the Grid addon UI and stored in
the addon's encrypted ``cli_config`` field; secrets never touch argv
(they travel in the provision env-file and via stdin-piped writes).

Install/config references (verified 2026-10-02):
- OPENCODE: https://opencode.ai/docs (install script + opencode.json)
- COMMANDCODE: https://commandcode.ai/docs (npm + config.json/auth.json)
- ANTIGRAVITYCLI: https://antigravity.google/download (install.sh, `agy`)
- KIMCHI: https://docs.kimchi.dev/docs/kimchi-cli (install.sh, `kimchi`)
- FORGECODE: https://forgecode.dev/docs (cli script, `forge`)
- DEEPAGENTS: langchain blog (uv tool install, `deepagents`)
- QWENCODE: https://github.com/QwenLM/qwen-code (npm, `qwen`)
- FACTORYDROID: https://docs.factory.com (npm `droid`, settings.json)
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

CLI_STATUS_PORT = 8686
CLI_BASE_IMAGE = "node:22-bookworm-slim"

CLI_ADDON_TYPES = frozenset({
    "OPENCODE",
    "COMMANDCODE",
    "ANTIGRAVITYCLI",
    "KIMCHI",
    "FORGECODE",
    "DEEPAGENTS",
    "QWENCODE",
    "FACTORYDROID",
})

# model-prefix -> conventional provider API-key env var (opencode reads
# provider keys from the environment). Unknown prefixes fall back to
# the caller-supplied api_key_env.
OPENCODE_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "ollama": "",
}

# Per-type spec: binary, first-boot install, readiness probe, default
# key env, and whether the UI model field maps to a real config file
# (False = stored + passed through as env only, auth is interactive).
_CLI_SPECS = {
    "OPENCODE": {
        "binary": "opencode",
        "install": "curl -fsSL https://opencode.ai/install | bash",
        "ready": "opencode --version",
        "key_env": "ANTHROPIC_API_KEY",
        "auth": "env",
        "model_file": True,
    },
    "COMMANDCODE": {
        "binary": "cmd",
        "install": "npm i -g command-code@latest",
        "ready": "cmd --version",
        "key_env": "COMMAND_CODE_API_KEY",
        "auth": "env",
        "model_file": True,
    },
    "ANTIGRAVITYCLI": {
        "binary": "agy",
        "install": (
            "curl -fsSL https://antigravity.google/cli/install.sh"
            " | bash -s -- --dir /data/bin"),
        "ready": "agy --version",
        "key_env": "AGY_API_KEY",
        "auth": "interactive",
        "model_file": False,
    },
    "KIMCHI": {
        "binary": "kimchi",
        "install": (
            "curl -fsSL https://github.com/getkimchi/kimchi/releases"
            "/latest/download/install.sh | bash"),
        "ready": "kimchi version",
        "key_env": "KIMCHI_API_KEY",
        "auth": "env",
        "model_file": False,
    },
    "FORGECODE": {
        "binary": "forge",
        "install": "curl -fsSL https://forgecode.dev/cli | sh",
        "ready": "forge --help",
        "key_env": "FORGE_API_KEY",
        "auth": "interactive",
        "model_file": False,
    },
    "DEEPAGENTS": {
        "binary": "deepagents",
        "install": (
            "command -v uv >/dev/null 2>&1 || "
            "(curl -LsSf https://astral.sh/uv/install.sh | sh); "
            "uv tool install deepagents-cli"),
        "ready": "command -v deepagents",
        "key_env": "DEEPAGENTS_API_KEY",
        "auth": "env",
        "model_file": False,
    },
    "QWENCODE": {
        "binary": "qwen",
        "install": "npm i -g @qwen-code/qwen-code@latest",
        "ready": "qwen --version",
        "key_env": "QWEN_API_KEY",
        "auth": "interactive",
        "model_file": False,
    },
    "FACTORYDROID": {
        "binary": "droid",
        "install": "npm i -g droid@latest",
        "ready": "droid --version",
        "key_env": "FACTORY_API_KEY",
        "auth": "interactive",
        "model_file": True,
    },
}

# Container-side absolute paths (HOME=/root in the node image).
OPENCODE_JSON = "/root/.config/opencode/opencode.json"
COMMANDCODE_JSON = "/root/.commandcode/config.json"
KIMCHI_JSON = "/root/.config/kimchi/config.json"
FACTORY_JSON = "/root/.factory/settings.json"

_SAFE_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/:@]{0,127}$")


def is_cli_addon(addon_type: str) -> bool:
    return (addon_type or "").upper() in CLI_ADDON_TYPES


def _spec(addon_type: str) -> dict:
    return _CLI_SPECS.get((addon_type or "").upper(), {})


def cli_binary(addon_type: str) -> str:
    return str(_spec(addon_type).get("binary", ""))


def default_key_env(addon_type: str) -> str:
    return str(_spec(addon_type).get("key_env", ""))


def model_file_supported(addon_type: str) -> bool:
    return bool(_spec(addon_type).get("model_file", False))


def auth_mode(addon_type: str) -> str:
    return str(_spec(addon_type).get("auth", "interactive"))


def suggest_key_env(addon_type: str, model: str) -> str:
    """Conventional key env for a model id (opencode ``provider/model``)."""
    kind = (addon_type or "").upper()
    if kind == "COMMANDCODE":
        return "COMMAND_CODE_API_KEY"
    if kind == "KIMCHI":
        return "KIMCHI_API_KEY"
    if kind == "OPENCODE" and model and "/" in model:
        prefix = model.split("/", 1)[0].strip().lower()
        mapped = OPENCODE_PROVIDER_KEY_ENV.get(prefix)
        if mapped:
            return mapped
    return default_key_env(kind)


def validate_cli_config(addon_type: str, data: dict) -> dict:
    """Validate + normalize user-supplied CLI config. Raises ValueError."""
    kind = (addon_type or "").upper()
    if kind not in CLI_ADDON_TYPES:
        raise ValueError(f"{addon_type} is not a CLI addon type")
    data = dict(data or {})
    out: dict[str, str] = {}
    # Empty string clears the stored key; None/omitted keeps it.
    if "api_key" in data:
        api_key = str(data.get("api_key") or "")
        if api_key and (len(api_key) > 4096 or any(ord(c) < 32 for c in api_key)):
            raise ValueError("api_key is unusable (empty, too long, or has control chars)")
        out["api_key"] = api_key
    api_key_env = str(data.get("api_key_env") or "").strip().upper()
    if api_key_env:
        if not _SAFE_ENV_NAME.match(api_key_env):
            raise ValueError(f"api_key_env {api_key_env!r} is not a safe env name")
        out["api_key_env"] = api_key_env
    model = str(data.get("model") or "").strip()
    if model:
        if not _SAFE_MODEL.match(model):
            raise ValueError(f"model {model!r} looks unsafe")
        out["model"] = model
    provider = str(data.get("provider") or "").strip().lower()
    if provider:
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,31}", provider):
            raise ValueError(f"provider {provider!r} looks unsafe")
        out["provider"] = provider
    return out


def merge_stored_config(stored: dict, update: dict) -> dict:
    """Merge a validated partial update onto the stored config."""
    merged = dict(stored or {})
    for key in ("api_key_env", "model", "provider"):
        if key in update:
            if update[key]:
                merged[key] = update[key]
            else:
                merged.pop(key, None)
    if "api_key" in update:
        # Empty string clears; omitted (absent) keeps.
        if update["api_key"]:
            merged["api_key"] = update["api_key"]
        else:
            merged.pop("api_key", None)
    return merged


def public_view(addon_type: str, stored: dict) -> dict:
    """GET view: shape + masked secrets (never leaks the key)."""
    kind = (addon_type or "").upper()
    stored = dict(stored or {})
    return {
        "addon_type": kind,
        "binary": cli_binary(kind),
        "auth_mode": auth_mode(kind),
        "model_file": model_file_supported(kind),
        "configured": bool(stored.get("api_key") or stored.get("model")),
        "model": stored.get("model", ""),
        "provider": stored.get("provider", ""),
        "api_key_env": stored.get("api_key_env") or default_key_env(kind),
        "api_key_set": bool(stored.get("api_key")),
        "suggested_env": suggest_key_env(kind, stored.get("model", "")),
    }


def container_files(addon_type: str, stored: dict) -> dict[str, str]:
    """Config files to write inside the addon container (path -> content)."""
    kind = (addon_type or "").upper()
    stored = dict(stored or {})
    files: dict[str, str] = {}
    model = stored.get("model", "")
    if kind == "OPENCODE" and model:
        files[OPENCODE_JSON] = json.dumps(
            {"$schema": "https://opencode.ai/config.json", "model": model},
            indent=2) + "\n"
    elif kind == "COMMANDCODE" and (model or stored.get("provider")):
        doc: dict[str, str] = {}
        if stored.get("provider"):
            doc["provider"] = stored["provider"]
        if model:
            doc["model"] = model
        files[COMMANDCODE_JSON] = json.dumps(doc, indent=2) + "\n"
    elif kind == "KIMCHI" and stored.get("api_key"):
        # Documented: global config holds the API key; KIMCHI_API_KEY
        # env takes precedence when both are set.
        files[KIMCHI_JSON] = json.dumps(
            {"apiKey": stored["api_key"]}, indent=2) + "\n"
    elif kind == "FACTORYDROID" and model:
        # Documented: ~/.factory/settings.json {"model": "<id>"}.
        files[FACTORY_JSON] = json.dumps({"model": model}, indent=2) + "\n"
    # ANTIGRAVITYCLI / FORGECODE / DEEPAGENTS / QWENCODE: auth and model
    # selection are interactive (or provider-env driven); the stored key
    # is injected as container env at provision (see provision_env).
    return files


def provision_env(stored: dict) -> dict[str, str]:
    """Extra container env derived from the CLI config (api key only)."""
    stored = dict(stored or {})
    api_key = stored.get("api_key", "")
    api_key_env = stored.get("api_key_env", "")
    if api_key and api_key_env and _SAFE_ENV_NAME.match(api_key_env):
        return {api_key_env: api_key}
    return {}


_STATUS_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="5" fill="#0b0f14"/><path d="m6 9 3 3-3 3" stroke="#5ec8f2" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><line x1="11" y1="15" x2="18" y2="15" stroke="#5ec8f2" stroke-width="2" stroke-linecap="round"/></svg>"""


_STATUS_JS = """const http=require('http');
const fs=require('fs');
let FAVICON='';
try{FAVICON=fs.readFileSync('/data/favicon.svg','utf8')}catch(e){}
const {execFileSync}=require('child_process');
const PORT=parseInt(process.env.CLI_PORT||'8686',10);
const SPECS=JSON.parse(process.env.CLI_SPECS||'[]');
function probe(bin,ready){try{const a=String(ready||'--version').split(' ').filter(Boolean);if(!bin)return null;const out=execFileSync(bin,a.slice(1),{timeout:8000}).toString().trim().slice(0,120);return out||'?'}catch(e){return null}}
function state(){return {status:'ok',clis:SPECS.map(s=>{const v=probe(s.binary,s.ready);return {type:s.type,binary:s.binary,version:v,installed:v!==null}})}}
const DOCS={OPENCODE:'https://opencode.ai/docs',COMMANDCODE:'https://commandcode.ai/docs',ANTIGRAVITYCLI:'https://antigravity.google/docs/cli/overview/',KIMCHI:'https://docs.kimchi.dev/docs/kimchi-cli',FORGECODE:'https://forgecode.dev/docs/',DEEPAGENTS:'https://docs.langchain.com/oss/python/deepagents/',QWENCODE:'https://github.com/QwenLM/qwen-code',FACTORYDROID:'https://docs.factory.com/droid-cli/overview'};
function page(){const s=state();const rows=s.clis.map(c=>`<tr><td>${c.type}</td><td><code>${c.binary}</code></td><td>${c.installed?('✅ '+(c.version||'installed')):'⏳ installing…'}</td><td><a href="${DOCS[c.type]||'#'}">docs</a></td></tr>`).join('');return `<!doctype html><html><head><meta charset=utf8><title>Grid CLI Runners</title><link rel="icon" href="/favicon.ico"><style>body{font-family:system-ui;background:#0b0f14;color:#dbe4ee;padding:32px}table{border-collapse:collapse}td,th{border:1px solid #26313d;padding:8px 12px}a{color:#5ec8f2}code{background:#141b24;padding:2px 6px;border-radius:4px}</style></head><body><h1>🤖 Grid CLI Runners</h1><p>Internal-only AI coding agents managed by Grid. Configure API keys &amp; models from the Grid dashboard (addon → CLI config).</p><table><tr><th>Addon</th><th>Binary</th><th>Status</th><th>Docs</th></tr>${rows}</table><p><a href="/api/status">JSON API</a></p></body></html>`}
http.createServer((q,r)=>{if(q.url==='/favicon.ico'){r.writeHead(200,{'content-type':'image/svg+xml'});r.end(FAVICON);return}if(q.url==='/api/status'){const s=state();const any=s.clis.some(c=>c.installed);r.writeHead(any?200:503,{'content-type':'application/json'});r.end(JSON.stringify(s));return}r.writeHead(200,{'content-type':'text/html'});r.end(page())}).listen(PORT);
"""


def entrypoint_script(addon_type: str = "") -> str:
    """Shared-container entrypoint: install ALL CLIs if missing, serve, wait.

    One container per service hosts every CLI addon (resource
    consolidation) — installs loop over all specs idempotently, so
    adding another CLI addon later costs no new container.
    """
    parts = [
        "set -e",
        "export PATH=\"/data/bin:$HOME/.local/bin:$HOME/.opencode/bin:/usr/local/bin:$PATH\"",
        "mkdir -p /data/bin \"$HOME/.local/bin\" /data/cli-home",
        "command -v curl >/dev/null 2>&1 || "
        "(apt-get update && apt-get install -y --no-install-recommends curl ca-certificates)",
    ]
    for kind in sorted(CLI_ADDON_TYPES):
        spec = _CLI_SPECS[kind]
        binary = spec["binary"]
        parts.append(
            f"if ! command -v {binary} >/dev/null 2>&1; then {spec['install']}; fi")
    parts += [
        # Symlink every CLI into /usr/local/bin: `docker exec` (console,
        # health probes, backup scripts) uses the default PATH, which does
        # not include ~/.opencode/bin, ~/.local/bin or /data/bin. Without
        # this the CLIs only resolve inside the entrypoint itself.
        "for _b in opencode cmd agy kimchi forge deepagents qwen droid; do",
        "  _p=$(command -v \"$_b\" 2>/dev/null || true);",
        '  if [ -n "$_p" ] && [ "$_p" != /usr/local/bin/* ]; then ln -sf "$_p" /usr/local/bin/"$_b"; fi;',
        "done",
    ]
    parts += [
        "cat > /data/cli-status.js <<'CLI_STATUS_EOF'",
        _STATUS_JS.rstrip("\n"),
        "CLI_STATUS_EOF",
        "cat > /data/favicon.svg <<'CLI_FAVICON_EOF'",
        _STATUS_FAVICON_SVG.rstrip("\n"),
        "CLI_FAVICON_EOF",
        f"CLI_SPECS='{status_specs_json()}' CLI_PORT={CLI_STATUS_PORT} "
        "exec node /data/cli-status.js",
    ]
    return "\n".join(parts) + "\n"


def status_specs_json() -> str:
    """Compact spec list for the status server (type/binary/ready)."""
    return json.dumps([
        {"type": t, "binary": _CLI_SPECS[t]["binary"],
         "ready": _CLI_SPECS[t]["ready"]}
        for t in sorted(CLI_ADDON_TYPES)
    ], separators=(",", ":"))


_CLI_PATH_EXPORT = (
    'export PATH="/data/bin:$HOME/.local/bin:$HOME/.opencode/bin:'
    '/usr/local/bin:$PATH"; '
)


def generic_config(addon_type: str) -> dict:
    """GENERIC_ADDONS_CONFIG entry for a CLI addon type.

    ``dashboard_port`` is set so the status/API page can be exposed as
    a URL on demand; addons stay internal-only until exposed.
    """
    kind = (addon_type or "").upper()
    spec = _spec(kind)
    if not spec:
        raise ValueError(f"{addon_type} is not a CLI addon type")
    probes = " && ".join(
        f"{_CLI_PATH_EXPORT}{_CLI_SPECS[t]['ready']}"
        for t in sorted(CLI_ADDON_TYPES)
    )
    return {
        "image": CLI_BASE_IMAGE,
        "port": CLI_STATUS_PORT,
        "dashboard_port": CLI_STATUS_PORT,
        "env_url": f"{kind}_URL",
        "scheme": "http",
        "auth": False,
        "health_timeout": 600,
        "ready_timeout": 600,
        # Login shells don't inherit the entrypoint's PATH additions,
        # so export explicitly (agy/kimchi live outside /usr/local/bin).
        "ready_cmd": probes,
        "command": ["bash", "-c", entrypoint_script()],
    }


def shared_container_name(service) -> str:
    """Stable shared-container name for a service's CLI addons."""
    sid = str(getattr(service, "id", "") or "unknown")
    return f"smsly-addon-cli-{sid}"


def resolve_container_name(addon) -> str:
    """Backing container for an addon (shared for CLI types)."""
    if is_cli_addon(getattr(addon, "addon_type", "")):
        return shared_container_name(getattr(addon, "service", None))
    return (f"smsly-addon-{str(getattr(addon, 'addon_type', '')).lower()}-"
            f"{getattr(addon, 'id', '')}")


def sibling_cli_addons(addon, include_self: bool = False) -> list:
    """Other ACTIVE CLI addons of the same service (for share decisions)."""
    try:
        from apps.deployments.models.addons import Addon as _AM
        service = getattr(addon, "service", None)
        if service is None:
            return []
        qs = _AM.objects.filter(
            service=service, status=_AM.Status.ACTIVE,
            addon_type__in=sorted(CLI_ADDON_TYPES),
        )
        if not include_self and getattr(addon, "pk", None):
            qs = qs.exclude(pk=addon.pk)
        return list(qs)
    except Exception as exc:
        logger.debug("sibling CLI lookup skipped: %s", exc)
        return []


def merged_cli_env(service) -> dict[str, str]:
    """Union of every ACTIVE CLI addon key env for a service."""
    merged: dict[str, str] = {}
    try:
        from apps.deployments.models.addons import Addon as _AM
        for row in _AM.objects.filter(
                service=service, status=_AM.Status.ACTIVE,
                addon_type__in=sorted(CLI_ADDON_TYPES)):
            for key, val in provision_env(read_stored_config(row)).items():
                merged[key] = val
    except Exception as exc:
        logger.debug("merged CLI env skipped: %s", exc)
    return merged


def _router_suffix(addon) -> str:
    name = str(getattr(addon, "name", "") or "cli")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "cli"
    return f"{slug}-{str(getattr(addon, 'id', ''))[:8]}"


def exposed_routers(addon) -> list[tuple[str, str]]:
    """[(router_suffix, domain)] for ACTIVE CLI siblings with public domains.

    Excludes ``addon`` itself (the caller handles its own rule).
    """
    routers: list[tuple[str, str]] = []
    for sib in sibling_cli_addons(addon, include_self=False):
        domain = str(getattr(sib, "public_domain", "") or "").strip()
        if domain:
            routers.append((_router_suffix(sib), domain))
    return routers


def sibling_aliases(addon) -> list[str]:
    """Network aliases every ACTIVE CLI addon of the service must hold."""
    aliases: list[str] = []
    for row in sibling_cli_addons(addon, include_self=True):
        host = str(getattr(row, "name", "") or "").strip()
        try:
            from urllib.parse import urlparse as _up
            host = _up(str(getattr(row, "connection_url", "") or "")).hostname or host
        except Exception:
            pass
        if host and host not in aliases:
            aliases.append(host)
    return aliases


def detach_cli_alias_remote(addon, server) -> None:
    """Strip one CLI addon's alias on a REMOTE node (SSH, best-effort)."""
    try:
        from urllib.parse import urlparse as _up
        from apps.deployments.services.ssh_client import SSHClient
        host = _up(str(getattr(addon, "connection_url", "") or "")).hostname
        host = host or str(getattr(addon, "name", "") or "").strip()
        if not host:
            return
        container = resolve_container_name(addon)
        ssh = SSHClient(
            ip=server.host, password=server.ssh_password,
            user=server.ssh_user, port=server.ssh_port,
            key_content=server.ssh_key, wg_address=server.wg_address,
        )
        ssh.connect()
        try:
            out, _, _ = ssh.exec_command(
                f"docker inspect -f '{{{{json .NetworkSettings.Networks}}}}' "
                f"{container} 2>/dev/null",
                timeout=30, raise_on_error=False)
            import json as _json
            try:
                nets = _json.loads((out or "").strip() or "{}")
            except (ValueError, TypeError):
                nets = {}
            for net, info in (nets or {}).items():
                aliases = (info or {}).get("Aliases", []) or []
                if host not in aliases:
                    continue
                kept = [a for a in aliases if a != host]
                ssh.exec_command(
                    f"docker network disconnect {net} {container} 2>/dev/null",
                    timeout=30, raise_on_error=False)
                if kept:
                    alias_args = " ".join(f"--alias {a}" for a in kept)
                    ssh.exec_command(
                        f"docker network connect {alias_args} {net} {container} 2>/dev/null",
                        timeout=30, raise_on_error=False)
                logger.info("Detached remote CLI alias %s (%s)", host, net)
        finally:
            ssh.close()
    except Exception as exc:
        logger.debug("Remote CLI alias detach skipped: %s", exc)


def detach_cli_alias(addon) -> None:
    """Best-effort: strip this addon's alias off the shared CLI container."""
    try:
        from apps.addons.services.addon_migrate import (
            container_network_aliases as _cna,
            strip_alias as _strip,
        )
        from urllib.parse import urlparse as _up
        host = _up(str(getattr(addon, "connection_url", "") or "")).hostname
        host = host or str(getattr(addon, "name", "") or "").strip()
        if not host:
            return
        container = resolve_container_name(addon)
        for net, aliases in _cna(container).items():
            if host in (aliases or []):
                _strip(container, net, host)
    except Exception as exc:
        logger.debug("CLI alias detach skipped: %s", exc)


def delete_shared_resources(addon, remove_container) -> bool:
    """Hard-delete path for one CLI addon on the shared container.

    Strips this addon's alias; removes the shared container + data
    volume only when no ACTIVE CLI sibling remains. ``remove_container``
    is ``orchestrator._safe_remove_container``-shaped (name -> bool);
    the volume is removed by name ``{shared}-data`` via
    ``remove_volume`` when provided, else docker CLI.
    """
    detach_cli_alias(addon)
    if sibling_cli_addons(addon, include_self=False):
        logger.info("CLI shared container kept (%s has siblings)",
                    resolve_container_name(addon))
        return True
    container = resolve_container_name(addon)
    ok = remove_container(container)
    if not ok:
        return False
    try:
        import subprocess as _sp
        _sp.run(["docker", "volume", "rm", f"{container}-data"],
                capture_output=True, timeout=60)
    except Exception as exc:
        logger.debug("CLI shared volume removal skipped: %s", exc)
    return True


def read_stored_config(addon) -> dict:
    """Safely decode an addon's encrypted cli_config JSON ({} when unset)."""
    raw = str(getattr(addon, "cli_config", "") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return dict(data) if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("Addon %s has unreadable cli_config; ignoring",
                       getattr(addon, "id", "?"))
        return {}


def _docker(*args: str, input_bytes: bytes | None = None) -> tuple[int, str]:
    import subprocess as _sp
    try:
        proc = _sp.run(["docker", *args], input=input_bytes,
                       capture_output=True, timeout=60)
    except (OSError, _sp.SubprocessError) as exc:
        return 1, str(exc)[:200]
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        return proc.returncode, (err or out).strip()[:200]
    return 0, out


def push_cli_files(container_name: str, addon_type: str, stored: dict) -> None:
    """Write CLI config files into a RUNNING addon container.

    Secrets travel via stdin pipes, never argv. Raises RuntimeError on
    failure (callers decide fail vs warn).
    """
    files = container_files(addon_type, stored)
    if not files:
        return
    code, _ = _docker("exec", container_name, "true")
    if code != 0:
        raise RuntimeError(f"container {container_name} is not reachable")
    if (addon_type or "").upper() in ("COMMANDCODE", "FACTORYDROID"):
        # Merge model/provider over any user-made settings instead of
        # clobbering them. Both CLIs document flat {"model": ...} files.
        path = COMMANDCODE_JSON if (addon_type or "").upper() == "COMMANDCODE" else FACTORY_JSON
        existing: dict = {}
        code, out = _docker("exec", container_name, "cat", path)
        if code == 0 and out.strip():
            try:
                parsed = json.loads(out)
                existing = parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                existing = {}
        try:
            wanted = json.loads(files[path])
        except (ValueError, KeyError):
            wanted = {}
        merged = {**existing, **wanted}
        files = {**files, path: json.dumps(merged, indent=2) + "\n"}
    for path, content in files.items():
        parent = path.rsplit("/", 1)[0]
        code, err = _docker("exec", container_name, "mkdir", "-p", parent)
        if code != 0:
            raise RuntimeError(f"mkdir {parent} failed: {err}")
        code, err = _docker("exec", "-i", container_name,
                            "sh", "-c", f"cat > {path} && chmod 600 {path}",
                            input_bytes=content.encode("utf-8"))
        if code != 0:
            raise RuntimeError(f"write {path} failed: {err}")
    logger.info("CLI config files pushed to %s (%d file(s))",
                container_name, len(files))
