# workflow
- Verify technical claims about third-party software syntax/behavior before making changes — especially when diagnosing production issues; consult documentation or the web rather than relying on assumptions. Confidence: 0.65
- Do not offer or attempt to SSH into the user's remote servers — debug and operate via their shell session or local access only. Confidence: 0.75
- Before committing changes to installer scripts (install.sh, lib/*.sh), thoroughly review exit code patterns and ensure failures warn but don't abort — do not commit until the error-handling flow is verified. Confidence: 0.80
- When stale or incorrect .env values are discovered (e.g., wrong hostnames, missing vars), add the fix to the installer scripts under lib/ rather than giving the user manual sed commands to run. Confidence: 0.85
- Review all unstaged changes before pushing — present a summary of each diff and wait for explicit approval before committing and pushing. Confidence: 0.70
- When writing tests that use the DRF test client, use `TestCase` + manual `APIClient` instead of `APITestCase` to avoid pytest-django DB serialization issues with missing migration columns (e.g., `traffic_geo_enabled`). Confidence: 0.70
