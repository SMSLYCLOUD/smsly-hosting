# Private Repository Setup - Trulay Grid

Since smsly-hosting is now a private repo, the VPS and Jules need authentication to access it.

---

## 1. VPS Deploy Key (for `install.sh --update`)

The VPS does `git pull` during deploys. It needs read-only access to the private repo.

### Step 1: Generate a deploy key on your VPS

```bash
ssh into your VPS first, then:

ssh-keygen -t ed25519 -f ~/.ssh/smsly_hosting_deploy -N "" -C "smsly-hosting-deploy-key"
```

### Step 2: Copy the public key

```bash
cat ~/.ssh/smsly_hosting_deploy.pub
```

Copy the output (starts with `ssh-ed25519 ...`).

### Step 3: Add it to GitHub

1. Go to: https://github.com/SMSLYCLOUD/smsly-hosting/settings/keys
2. Click **"Add deploy key"**
3. Title: `VPS Deploy Key`
4. Key: paste the public key from Step 2
5. ☐ Do NOT check "Allow write access" (read-only is safer)
6. Click **"Add key"**

### Step 4: Configure Git on the VPS to use the deploy key

```bash
# Option A: Set SSH command for this repo only
cd /opt/smsly-hosting
git config core.sshCommand "ssh -i ~/.ssh/smsly_hosting_deploy -o StrictHostKeyChecking=accept-new"

# Option B: Switch remote from HTTPS to SSH (if currently using HTTPS)
git remote set-url origin git@github.com:SMSLYCLOUD/smsly-hosting.git
```

### Step 5: Test

```bash
cd /opt/smsly-hosting
git fetch origin
# Should succeed without asking for password
```

---

## 2. GitHub Actions Secrets

Your `deploy.yml` already uses these secrets. Verify they're set:

1. Go to: https://github.com/SMSLYCLOUD/smsly-hosting/settings/secrets/actions
2. Confirm these secrets exist:

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `VPS_HOST` | Your VPS IP address (e.g., `123.45.67.89`) | Server to SSH into |
| `VPS_USER` | SSH username (e.g., `root` or `deploy`) | User for SSH login |
| `VPS_SSH_KEY` | Your SSH private key (full PEM content) | For appleboy/ssh-action |

### How to add/update a secret:

1. Go to the link above
2. Click **"New repository secret"**
3. Enter the name and value
4. Click **"Add secret"**

### For VPS_SSH_KEY specifically:

```bash
# On YOUR LOCAL machine (not VPS), generate a key if you don't have one:
ssh-keygen -t ed25519 -f ~/.ssh/smsly_deploy_ci -N ""

# Copy the PUBLIC key to the VPS:
ssh-copy-id -i ~/.ssh/smsly_deploy_ci.pub user@your-vps-ip

# Copy the PRIVATE key content — this goes into GitHub Secrets:
cat ~/.ssh/smsly_deploy_ci
# Copy everything including -----BEGIN OPENSSH PRIVATE KEY----- lines
```

Paste the full private key content as the `VPS_SSH_KEY` secret value on GitHub.

---

## 3. Jules Access

For Jules to work on this private repo:

1. Go to: https://github.com/apps/jules-by-google (or open Jules from your GitHub dashboard)
2. When creating a new Jules task, it will prompt you to grant access
3. Select the `SMSLYCLOUD` organization
4. Grant access to the `smsly-hosting` repository
5. Paste the contents of `JULES_TASK_TIER_SYSTEM.md` as the task description

---

## 4. Verify Everything Works

After completing the steps above, run a quick test:

```bash
# On VPS: Test git pull
cd /opt/smsly-hosting
git pull origin main

# On GitHub: Trigger a manual deploy
# Go to: Actions → Deploy to Production → Run workflow

# Jules: Create a task and verify it can read the repo
```

---

## Troubleshooting

### "Permission denied (publickey)" on VPS git pull
- Check the deploy key is added to GitHub
- Check `git config core.sshCommand` points to the correct key file
- Run: `ssh -i ~/.ssh/smsly_hosting_deploy -T git@github.com` (should say "successfully authenticated")

### GitHub Actions deploy fails with auth error
- The deploy action SSHes to VPS, then VPS does git pull
- The VPS deploy key (Step 1) must be set up correctly
- The GitHub Actions secrets (Step 2) must be set

### Jules can't access the repo
- Re-authorize the Jules GitHub App for the SMSLYCLOUD organization
- Make sure smsly-hosting is in the list of accessible repositories
