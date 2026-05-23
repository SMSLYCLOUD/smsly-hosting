"""Cost module."""
from typing import Dict, List, Any
from decimal import Decimal
from .providers import _cached_ask

class CostAdvisor:
    """
    Compares estimated costs across Cloud Providers.
    """

    # Simplified Pricing Model (USD per Hour)
    # AWS Fargate vCPU: ~$0.04048
    # AWS Fargate GB-RAM: ~$0.004445
    # GCP Cloud Run vCPU: ~$0.024
    # GCP Cloud Run GB-RAM: ~$0.0025
    # Railway: Usage based (CPU+RAM)

    PRICING = {
        'AWS': {'cpu': 0.04048, 'ram': 0.004445},
        'GCP': {'cpu': 0.02400, 'ram': 0.002500},
        # Azure Container Apps approx
        'AZURE': {'cpu': 0.04500, 'ram': 0.005000},
        'RAILWAY': {'cpu': 0.02000, 'ram': 0.002000},  # Placeholder
    }

    def estimate_monthly_cost(self, cpu_count: float,
                              memory_gb: float) -> Dict[str, Decimal]:
        """
        Calculates estimated monthly cost (730 hours) for a given resource configuration.
        """
        hours = 730
        estimates = {}

        for provider, rates in self.PRICING.items():
            cpu_cost = Decimal(str(rates['cpu'])) * \
                Decimal(str(cpu_count)) * hours
            ram_cost = Decimal(str(rates['ram'])) * \
                Decimal(str(memory_gb)) * hours
            total = (cpu_cost + ram_cost).quantize(Decimal("0.01"))
            estimates[provider] = total

        return estimates

    def ai_cost_analysis(self, service_config: dict) -> str:
        """Use AI to provide detailed cost optimization recommendations."""
        prompt = (
            f"Given this service configuration:\n"
            f"- CPU: {service_config.get('cpu_cores', 1)} cores\n"
            f"- Memory: {service_config.get('memory_mb', 512)}MB\n"
            f"- Stack: {service_config.get('stack', 'unknown')}\n"
            f"- Current provider: {service_config.get('provider', 'unknown')}\n\n"
            f"Provide 3 specific cost optimization recommendations. "
            f"Compare AWS vs GCP vs Railway pricing. Be concise."
        )
        try:
            response, provider = _cached_ask(prompt)
            return f"[{provider}] {response}"
        except Exception:
            return self._fallback_advice(service_config)

    def _fallback_advice(self, config: dict) -> str:
        estimates = self.estimate_monthly_cost(
            float(config.get('cpu_cores', 1)),
            float(config.get('memory_mb', 512)) / 1024
        )
        if not estimates:
            return "No estimates available."
        cheapest = min(estimates, key=estimates.get)
        return f"Cheapest option: {cheapest} at ${estimates[cheapest]}/mo"
