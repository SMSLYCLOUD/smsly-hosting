import logging

from .models import DiagnosticResult

logger = logging.getLogger(__name__)


class AIEscalationMixin:

    def escalate_to_ai(self, deployment, diagnostics: DiagnosticResult) -> dict:
        """
        Escalate to system intelligence (AI) for advanced diagnosis.

        Gathers all diagnostic context and sends to the AI router for
        analysis and remediation commands.
        """
        self._log("Escalating to system intelligence (AI)")

        context = {
            "server": {
                "name": self.server.name,
                "host": self.server.host,
                "is_lite_agent": getattr(self.server, "is_lite_agent", False),
            },
            "deployment": {
                "id": str(getattr(deployment, "id", "")),
                "status": getattr(deployment, "status", ""),
                "container_id": getattr(deployment, "container_id", ""),
            },
            "diagnostics": {
                "failure_type": diagnostics.failure_type.value,
                "container_state": diagnostics.container_state,
                "container_logs": diagnostics.container_logs[-5000:],
                "disk_usage_pct": diagnostics.disk_usage_pct,
                "memory_usage_pct": diagnostics.memory_usage_pct,
                "docker_running": diagnostics.docker_running,
                "network_reachable": diagnostics.network_reachable,
                "error_details": diagnostics.error_details,
            },
            "heal_log": self._heal_log[-20:],
        }

        try:
            # Note: AIProviderSettings is not available in agent mode
            try:
                from apps.intelligence.models import AIProviderSettings
            except (ImportError, RuntimeError):
                self._log("Intelligence app not available in agent mode — cannot escalate to AI")
                return {"success": False, "error": "Intelligence app not available in agent mode"}

            ai_settings = AIProviderSettings.get_solo()
            has_api_key = bool(
                ai_settings.openai_api_key or ai_settings.grok_api_key
                or ai_settings.gemini_api_key or ai_settings.claude_api_key
                or ai_settings.deepseek_api_key or ai_settings.openrouter_api_key
                or ai_settings.groq_api_key or ai_settings.alibaba_api_key
                or ai_settings.jules_api_key or ai_settings.localllm_api_key
                or ai_settings.smslycloud_api_key
            )
            if not has_api_key:
                self._log("No active AI provider — cannot escalate")
                return {"success": False, "error": "No active AI provider"}

            prompt = self._build_ai_prompt(context)

            try:
                from apps.intelligence.providers import ask_with_fallback

                system_prompt = (
                    "You are the SMSLY AI Senate Committee — a panel of AI experts "
                    "collaborating on DevOps diagnosis and recovery.\n\n"
                    "RULES:\n"
                    "1. Analyze the provided diagnostic data thoroughly.\n"
                    "2. If a command suggestion is appropriate, prefix each command with 'CMD:'.\n"
                    "3. Be specific about root cause, not vague.\n"
                    "4. Suggest commands that are safe to run via SSH on a production server.\n"
                    "5. Consider all self-healing actions already attempted in the heal log."
                )

                ai_response, provider_name = ask_with_fallback(
                    prompt, system_prompt=system_prompt, mode="senate"
                )

                self._log(f"Senate Committee response from {provider_name} ({len(ai_response)} chars)")

                commands = self._extract_commands(ai_response)
                return {
                    "success": True,
                    "ai_response": ai_response,
                    "suggested_commands": commands,
                    "provider": provider_name,
                }

            except Exception as exc:
                self._log(f"AI escalation failed: {exc}")

        except Exception as exc:
            self._log(f"AI escalation setup failed: {exc}")

        return {"success": False, "error": "AI escalation failed"}

    def _build_ai_prompt(self, context: dict) -> str:
        """Build a prompt for the AI with full diagnostic context."""
        return f"""You are an expert DevOps engineer diagnosing a failed deployment on a remote server.

SERVER: {context['server']['name']} ({context['server']['host']})
DEPLOYMENT ID: {context['deployment']['id']}
FAILURE TYPE: {context['diagnostics']['failure_type']}

CONTAINER STATE: {context['diagnostics']['container_state']}
DISK USAGE: {context['diagnostics']['disk_usage_pct']}%
MEMORY USAGE: {context['diagnostics']['memory_usage_pct']}%
DOCKER RUNNING: {context['diagnostics']['docker_running']}
NETWORK REACHABLE: {context['diagnostics']['network_reachable']}

CONTAINER LOGS (last 5000 chars):
{context['diagnostics']['container_logs']}

HEAL ATTEMPTS ALREADY MADE:
{chr(10).join('- ' + entry for entry in context['heal_log'][-10:])}

Analyze the issue and provide:
1. Root cause diagnosis
2. Specific shell commands to fix the issue (one per line, prefixed with CMD:)
3. Verification commands to confirm the fix worked

The server uses Docker Compose. The hosting path is likely /opt/smsly-hosting.
Be specific and actionable. Focus on commands that can be run via SSH.
"""

    def _extract_commands(self, ai_response: str) -> list[str]:
        """Extract commands from AI response."""
        commands = []
        for line in ai_response.split("\n"):
            line = line.strip()
            if line.upper().startswith("CMD:"):
                commands.append(line[4:].strip())
            elif line.startswith("$ ") or line.startswith("# "):
                commands.append(line[2:].strip())
        return commands
