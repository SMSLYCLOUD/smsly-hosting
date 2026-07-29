# Dependencies: pip install pandas tabulate
import pandas as pd
from tabulate import tabulate

# --- Constants & Pricing Models ---

# Grid Managed: Pro Plan ($29) + Usage
CN_MANAGED_BASE = 29.00
CN_CPU_RATE = 0.01      # per vCPU/hour
CN_RAM_RATE = 0.005     # per GB/hour

# Grid Self-Hosted (VPS Cost approximation - e.g., Hetzner/DO)
# Assuming efficient bin-packing on standard VPS sizes
# e.g. $6/mo for 2vCPU/4GB (Hetzner CPX21 is ~€8)
# Let's average $4 per vCPU/2GB unit.
CN_SELF_HOSTED_UNIT_COST = 4.00 # Per "Unit" of ~1vCPU/2GB

# AWS Fargate (us-east-1, Linux x86)
AWS_CPU_RATE = 0.04048
AWS_RAM_RATE = 0.004445

# GCP Cloud Run (Tier 1, always allocated CPU for fair comparison)
GCP_CPU_RATE = 0.0588    # Approximate
GCP_RAM_RATE = 0.0090    # Approximate

# Railway (Developer Plan)
# ~$0.002/vCPU/min -> $0.12/hour (very expensive for sustained)
# ~$0.0002/GB/min -> $0.012/hour
RAILWAY_CPU_RATE = 0.12
RAILWAY_RAM_RATE = 0.012

# Vercel (Pro Plan)
# $20/seat + Serverless Function Execution
# Hard to compare 1:1 with long-running services, but we'll approximate based on GB-hours
# $0.40 per 100 GB-hours -> $0.004/GB/hour
# CPU is often bundled or abstract, but let's add a base overhead.
VERCEL_BASE = 20.00
VERCEL_GB_HOUR_RATE = 0.004
# Vercel functions are ephemeral, so "CPU" cost is execution time.
# We'll assume equivalent compute cost to AWS Lambda (~$0.06/vCPU/hr equivalent)
VERCEL_CPU_EQUIV_RATE = 0.06

HOURS_PER_MONTH = 730

def calculate_monthly_cost(name, cpu, ram_gb, base_fee=0, cpu_rate=0, ram_rate=0, is_self_hosted=False):
    if is_self_hosted:
        # Simple bin-packing: You buy VPS instances.
        # 1 Unit = 1 vCPU, 2GB RAM = $5
        units_needed_cpu = cpu
        units_needed_ram = ram_gb / 2
        units = max(units_needed_cpu, units_needed_ram)
        # Round up to nearest unit (you can't buy half a VPS usually)
        import math
        units = math.ceil(units)
        return units * CN_SELF_HOSTED_UNIT_COST

    usage_cost = (cpu * cpu_rate * HOURS_PER_MONTH) + (ram_gb * ram_rate * HOURS_PER_MONTH)
    return base_fee + usage_cost

scenarios = [
    {"name": "Hobby (Side Project)", "cpu": 0.5, "ram": 0.5},
    {"name": "Startup (MVP)", "cpu": 2.0, "ram": 4.0},
    {"name": "Growth (Scale)", "cpu": 10.0, "ram": 32.0},
    {"name": "Enterprise (Heavy)", "cpu": 50.0, "ram": 128.0},
]

results = []

for s in scenarios:
    cpu = s["cpu"]
    ram = s["ram"]

    # Grid Self-Hosted
    cn_self = calculate_monthly_cost("CN Self-Hosted", cpu, ram, is_self_hosted=True)

    # Grid Managed
    cn_managed = calculate_monthly_cost("CN Managed", cpu, ram, base_fee=CN_MANAGED_BASE, cpu_rate=CN_CPU_RATE, ram_rate=CN_RAM_RATE)

    # AWS Fargate
    aws = calculate_monthly_cost("AWS Fargate", cpu, ram, cpu_rate=AWS_CPU_RATE, ram_rate=AWS_RAM_RATE)

    # GCP Cloud Run
    gcp = calculate_monthly_cost("GCP Cloud Run", cpu, ram, cpu_rate=GCP_CPU_RATE, ram_rate=GCP_RAM_RATE)

    # Railway
    railway = calculate_monthly_cost("Railway", cpu, ram, base_fee=5, cpu_rate=RAILWAY_CPU_RATE, ram_rate=RAILWAY_RAM_RATE) # $5 starter plan often included

    # Vercel
    vercel = calculate_monthly_cost("Vercel", cpu, ram, base_fee=VERCEL_BASE, cpu_rate=VERCEL_CPU_EQUIV_RATE, ram_rate=VERCEL_GB_HOUR_RATE)

    results.append({
        "Scenario": s["name"],
        "Resources": f"{cpu} vCPU / {ram} GB",
        "CN (Self-Hosted)": f"${cn_self:.2f}",
        "CN (Managed)": f"${cn_managed:.2f}",
        "AWS Fargate": f"${aws:.2f}",
        "GCP Cloud Run": f"${gcp:.2f}",
        "Railway": f"${railway:.2f}",
        "Vercel": f"${vercel:.2f}"
    })

df = pd.DataFrame(results)
markdown_table = tabulate(results, headers="keys", tablefmt="github")

print(markdown_table)

# Save to file
with open("docs/COMPETITOR_PRICING.md", "w") as f:
    f.write("# Grid Competitor Pricing Analysis\n\n")
    f.write(f"Generated on {pd.Timestamp.now()}\n\n")
    f.write(markdown_table)
    f.write("\n\n## Assumptions\n")
    f.write(f"* **Grid Self-Hosted**: ${CN_SELF_HOSTED_UNIT_COST}/mo per 1vCPU/2GB unit (VPS cost).\n")
    f.write(f"* **Grid Managed**: ${CN_MANAGED_BASE}/mo base + ${CN_CPU_RATE}/vCPU-hr + ${CN_RAM_RATE}/GB-hr.\n")
    f.write(f"* **AWS Fargate**: ${AWS_CPU_RATE}/vCPU-hr + ${AWS_RAM_RATE}/GB-hr (us-east-1).\n")
    f.write(f"* **GCP Cloud Run**: ${GCP_CPU_RATE}/vCPU-hr + ${GCP_RAM_RATE}/GB-hr.\n")
    f.write(f"* **Railway**: ${RAILWAY_CPU_RATE}/vCPU-hr + ${RAILWAY_RAM_RATE}/GB-hr.\n")
    f.write(f"* **Vercel**: ${VERCEL_BASE}/mo base + Usage estimates.\n")
