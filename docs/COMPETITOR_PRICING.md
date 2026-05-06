# Grid Competitor Pricing Analysis

Generated on 2026-02-14 03:25:26.166214

| Scenario             | Resources            | CN (Self-Hosted)   | CN (Managed)   | AWS Fargate   | GCP Cloud Run   | Railway   | Vercel   |
|----------------------|----------------------|--------------------|----------------|---------------|-----------------|-----------|----------|
| Hobby (Side Project) | 0.5 vCPU / 0.5 GB    | $4.00              | $34.48         | $16.40        | $24.75          | $53.18    | $43.36   |
| Startup (MVP)        | 2.0 vCPU / 4.0 GB    | $8.00              | $58.20         | $72.08        | $112.13         | $215.24   | $119.28  |
| Growth (Scale)       | 10.0 vCPU / 32.0 GB  | $64.00             | $218.80        | $399.34       | $639.48         | $1161.32  | $551.44  |
| Enterprise (Heavy)   | 50.0 vCPU / 128.0 GB | $256.00            | $861.20        | $1892.86      | $2987.16        | $5506.28  | $2583.76 |

## Assumptions
* **Grid Self-Hosted**: $4.0/mo per 1vCPU/2GB unit (VPS cost).
* **Grid Managed**: $29.0/mo base + $0.01/vCPU-hr + $0.005/GB-hr.
* **AWS Fargate**: $0.04048/vCPU-hr + $0.004445/GB-hr (us-east-1).
* **GCP Cloud Run**: $0.0588/vCPU-hr + $0.009/GB-hr.
* **Railway**: $0.12/vCPU-hr + $0.012/GB-hr.
* **Vercel**: $20.0/mo base + Usage estimates.
