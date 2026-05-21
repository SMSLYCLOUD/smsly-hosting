# Test Failing Service

This service intentionally crashes on boot with a syntax error in `index.js` (missing closing parenthesis on the route handler).

## Purpose

Used to verify the Jules auto-fix pipeline:

1. Deploy this service via the SMSLY UI (link a Git repo containing this code)
2. The deployment will fail with a syntax error
3. `_handle_failure` will trigger `jules_fix_deployment_failure`
4. Jules AI will analyze the logs and generate a fix
5. The fix will be pushed to a new branch and a PR created
6. If auto-deploy is enabled (`jules_auto_deploy_pr=True`), the fix branch is deployed automatically

## How to use

1. Push this directory to a Git repo
2. In the SMSLY UI, create a new service linked to that repo
3. Deploy the service — it will fail
4. Check the deployment logs for the Jules auto-fix trigger
