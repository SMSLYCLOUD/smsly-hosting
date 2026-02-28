# Troubleshooting Guide

This guide covers common issues and resolutions when operating CloudNeuron.

## Deployment Failures

### 1. Nixpacks Build Crashes
**Symptom:** `Nixpacks build failed` or out of memory during docker build.
**Resolution:** Enable swap memory on the server. The `install.sh` script does this automatically, but if you bypassed it, run:
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### 2. GitHub Webhook Failing (401 Unauthorized)
**Symptom:** Push to main doesn't trigger a build. GitHub shows red 'X' on webhook deliveries.
**Resolution:** The `GITHUB_WEBHOOK_SECRET` in your `.env` must exactly match the secret configured in the GitHub repository webhook settings.

## Platform Operations

### 1. Database Migrations Stuck
**Symptom:** `relation does not exist` errors.
**Resolution:** Run the migrations manually from the instance:
```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
```

### 2. OAuth Social Login Fails
**Symptom:** Clicking GitHub/Google login loops back to the login page or shows `400 Bad Request`.
**Resolution:** Ensure the `SITE_URL` in `.env` is set exactly to your domain including `https://` (e.g. `https://cloud.mycompany.com`). The OAuth callback URL registered in GitHub/Google must match `https://cloud.mycompany.com/api/v1/auth/github/callback/` or similar.

### 3. Caddy SSL Failing
**Symptom:** Site works on IP but domain doesn't load or shows invalid certificate.
**Resolution:** Ensure your DNS A record points to the server IP and port 80/443 are open. Check logs:
```bash
docker compose -f docker-compose.prod.yml logs caddy
```
