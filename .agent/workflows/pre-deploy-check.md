---
description: Verify production build passes before deploying
---

# Pre-Deployment Verification Workflow

## Purpose

Run all checks to ensure a clean production build before deploying.

// turbo-all

## Step 1: Lint check backend

```bash
cd backend
pip install flake8
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

## Step 2: Check Django configuration

```bash
cd backend
python manage.py check --deploy
```

## Step 3: Verify migrations are clean

```bash
cd backend
python manage.py makemigrations --check --dry-run
```

## Step 4: Build frontend for production

```bash
cd frontend
npm run build
```

## Step 5: Run frontend linting

```bash
cd frontend
npm run lint
```

## Step 6: Test Docker build (backend)

```bash
docker build -t smsly-backend-test ./backend
```

## Step 7: Test Docker build (frontend)

```bash
docker build -t smsly-frontend-test ./frontend
```

## Step 8: Test full stack with docker-compose

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
sleep 30
curl -f http://localhost/api/health/ && echo "✅ Backend healthy"
docker compose -f docker-compose.prod.yml down
```

## Success Criteria

- [ ] No flake8 errors
- [ ] Django `--deploy` check passes
- [ ] No pending migrations
- [ ] Frontend build succeeds (no ESLint errors)
- [ ] Docker images build successfully
- [ ] Full stack starts and responds to health check
