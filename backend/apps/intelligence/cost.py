from typing import Dict, List, Any
from decimal import Decimal

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
        'AZURE': {'cpu': 0.04500, 'ram': 0.005000}, # Azure Container Apps approx
        'RAILWAY': {'cpu': 0.02000, 'ram': 0.002000}, # Placeholder
    }

    def estimate_monthly_cost(self, cpu_count: float, memory_gb: float) -> Dict[str, Decimal]:
        """
        Calculates estimated monthly cost (730 hours) for a given resource configuration.
        """
        hours = 730
        estimates = {}

        for provider, rates in self.PRICING.items():
            cpu_cost = Decimal(str(rates['cpu'])) * Decimal(str(cpu_count)) * hours
            ram_cost = Decimal(str(rates['ram'])) * Decimal(str(memory_gb)) * hours
            total = (cpu_cost + ram_cost).quantize(Decimal("0.01"))
            estimates[provider] = total

        return estimates
