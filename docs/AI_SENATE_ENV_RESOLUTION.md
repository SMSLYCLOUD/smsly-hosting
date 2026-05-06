# AI Senate Environment Resolution

The Grid deployment orchestration leverages the existing AI Senate (via `apps.intelligence.providers.ask_with_fallback`) to propose intelligent ecosystem environment resolutions without exposing raw AI output.

## Architecture
- **EcosystemDeploymentSenate**: A custom adapter (`services/ecosystem_ai.py`) that formats a manifest summary and requests a strict JSON schema from the Senate.
- **Deterministic Validators**: AI-proposed configurations are strictly advisory. If the AI returns malformed data or if values violate deterministic rules (e.g. weak passwords), the strict environment resolver in `services/ecosystem_env.py` falls back to its deterministic generation logic or rejects the deployment entirely.
- **Security Guardrails**: Raw prompts, unredacted secrets, and chain-of-thought outputs are never logged, persisted, or returned to the user frontend.

## Flow
1. Ecosystem manifest is parsed into a graph.
2. A sanitised summary of the graph (services and addons) is sent to the `EcosystemDeploymentSenate`.
3. The AI provider returns a JSON payload matching the `{"resolutions": ...}` schema.
4. The output is extracted and parsed. If parsing fails, deterministic fallback immediately takes over.
