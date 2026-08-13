# Contributing to Trulay Grid

We are building a self-hosted application platform for teams operating across clouds, regions, and bare-metal infrastructure.

> **Naming:** **Trulay** is the company and **Trulay Grid** is the product. Use **Grid** only as a compact product label. Do not rename `SMSLY_*`, `smsly-*`, repository paths, package names, Docker resources, Celery task names, database objects, or other compatibility identifiers without an explicit migration plan. See [Brand and Naming](docs/BRAND_AND_NAMING.md).

## 🌟 Bounty Program
We incentivize critical features. Check our [Issues](https://github.com/SMSLYCLOUD/smsly-hosting/issues) for "Bounty" labels.
- **$50 - $500**: Creating new Cloud Adapters (e.g., DigitalOcean, Linode).
- **$100**: Dashboard UI improvements.
- **$1000**: Core architecture upgrades (e.g., Service Mesh integration).

## 🛠️ Development Setup

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (matches `Dockerfile` and `.devcontainer/devcontainer.json`)
- Node.js 20+

### Steps
1. **Fork & Clone**
   ```bash
   git clone https://github.com/YOUR_USERNAME/smsly-hosting.git
   cd smsly-hosting
   ```

2. **Start Backend**
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver
   ```

3. **Start Frontend**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## 🧪 Testing
We enforce strict quality standards.
- **Backend Tests**: `cd backend && pytest` (must pass CI; `pytest.ini` defines custom markers — `slow`, `integration`, `security`, `e2e`, `smoke`).
- **Linting**: `pylint` runs in CI as a blocking check; the config enables ~20 carefully chosen rules. A **9.0+** score is encouraged but not enforced (the legacy hard threshold was retired when the `.pylintrc` was tightened to disable noisy checks).

## 🌍 Global Mission
Our goal is to democratize cloud infrastructure. Avoid region-specific hardcoding unless explicitly handling latency optimization. Ensure UI supports i18n where possible.

---

## Code Style

Consistent code style keeps the codebase readable and makes reviews faster.

### Python (Backend)

- **Linter:** Ruff. Run before every commit:
  ```bash
  ruff check backend/
  ```
- **Indentation:** 4 spaces, no tabs.
- **Strings:** Double quotes (`"string"`), not single quotes.
- **Type hints:** Required for all new functions and public methods.
- **Imports:** Sorted by Ruff's isort rules. Run `ruff check --select I backend/` to auto-fix.
- **Docstrings:** Google-style docstrings for all public classes and functions.
- **Line length:** 120 characters max (configured in `pyproject.toml`).

### TypeScript / Frontend

- **Formatter:** Prettier. Runs on save in most editors.
- **Linter:** ESLint. Run before every commit:
  ```bash
  cd frontend && npm run lint
  ```
- **Indentation:** 2 spaces.
- **Semicolons:** Required (Prettier default).
- **Component style:** Functional components with hooks. No class components.

### Quick Lint Before Commit

```bash
# Backend
ruff check backend/

# Frontend
cd frontend && npm run lint
```

---

## PR Process

### Workflow

1. **Fork** the repository (if you haven't already).
2. **Create a branch** from `main` with the correct prefix:
   ```bash
   git checkout -b feat/add-redis-adapter main
   ```
3. **Make your changes**, following the code style above.
4. **Write or update tests** for your changes.
5. **Run linters and tests** locally (see sections above).
6. **Push** your branch and open a Pull Request.

### Branch Naming

Use one of these prefixes:

| Prefix | Use For | Example |
|--------|---------|---------|
| `feat/` | New features | `feat/add-linode-adapter` |
| `fix/` | Bug fixes | `fix/retry-logic-on-timeout` |
| `chore/` | Tooling, deps, CI | `chore/update-ruff-to-0.4` |
| `docs/` | Documentation only | `docs/update-api-reference` |
| `refactor/` | Code restructuring | `refactor/extract-service-layer` |
| `test/` | Adding/updating tests | `test/add-task-coverage` |

### PR Description Template

Use this template when opening a PR:

```markdown
## What
Brief description of the change.

## Why
Context or link to issue. Closes #123.

## How
Key implementation details.

## Checklist
- [ ] `ruff check backend/` passes
- [ ] `cd frontend && npm run lint` passes
- [ ] `pytest` passes
- [ ] Tests added for new functionality
- [ ] Documentation updated (if applicable)
```

### Code Review Expectations

- **All PRs require at least one approval** before merging.
- Reviewers will check for correctness, style, test coverage, and adherence to AGENTS.md lessons.
- Respond to all comments — either resolve them or explain why you disagree.
- Keep PRs focused. One logical change per PR. If you find an unrelated bug, file a separate issue.
- Do not self-merge without review, even if you have write access.

### Merge Strategy

We use **squash merge**. Your branch commits will be squashed into a single commit on `main`. This means:
- Your individual commit messages don't matter as much — focus on the PR title and description.
- The PR title becomes the commit message on `main`, so it must follow [Conventional Commits](#commit-messages).
- Feel free to push WIP commits to your branch; they'll be squashed away.

---

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/). This enables automated changelogs and semantic versioning.

### Format

```
<type>: <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Use For |
|------|---------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `chore` | Tooling, dependencies, CI config |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |

### Rules

- **Subject line:** Max 72 characters. Use imperative mood ("add", not "added").
- **Body:** Wrap at 80 characters. Explain _what_ and _why_, not _how_.
- **Reference issues:** Use `Closes #123` or `Fixes #123` in the footer.
- **Breaking changes:** Add `BREAKING CHANGE:` in the footer, or use `!` after the type (`feat!: remove legacy API`).

### Examples

```
feat: add DigitalOcean cloud adapter

Implement DOAdapter class with create/delete/list droplet support.
Includes token-based auth and retry logic.

Closes #42
```

```
fix: handle timeout in delete_service_task

Add soft_time_limit of 300s and catch SoftTimeLimitExceeded
to prevent worker thread from blocking indefinitely.

Fixes #87
```

```
chore: upgrade ruff to 0.4.0
```

---

## Running the Stack Locally

### Full Stack with Docker

The fastest way to get everything running:

```bash
docker compose up -d
```

This starts the backend, frontend, database, Redis, and Celery workers.

### Backend Only (without Docker)

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your local settings (DB credentials, secrets, etc.)

python manage.py migrate
python manage.py runserver
```

### Frontend Only

```bash
cd frontend
npm install
cp .env.example .env.local
# Edit .env.local if needed (API URL, etc.)
npm run dev
```

### Environment Setup

1. Copy `.env.example` to `.env` (backend) and `.env.local` (frontend).
2. At minimum, set:
   - `DATABASE_URL` — PostgreSQL connection string
   - `REDIS_URL` — Redis connection string
   - `SECRET_KEY` — Django secret key (generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`)
   - `ALLOWED_HOSTS` — Comma-separated list (use `localhost,127.0.0.1` for local dev)

---

## Adding a New API Endpoint

### Step 1: Create a Serializer

Create or extend a serializer in the relevant app's `serializers/` directory:

```python
# backend/apps/myapp/serializers/myresource.py
from rest_framework import serializers
from ..models import MyResource


class MyResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MyResource
        fields = ["id", "name", "created_at"]
        read_only_fields = ["id", "created_at"]
```

### Step 2: Create a View

```python
# backend/apps/myapp/views/myresource.py
from rest_framework import viewsets
from ..models import MyResource
from ..serializers.myresource import MyResourceSerializer


class MyResourceViewSet(viewsets.ModelViewSet):
    queryset = MyResource.objects.all()
    serializer_class = MyResourceSerializer
```

### Step 3: Register in urls.py

```python
# backend/apps/myapp/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.myresource import MyResourceViewSet

router = DefaultRouter()
router.register(r"myresources", MyResourceViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
```

### Step 4: Add Tests

```python
# backend/apps/myapp/tests/test_myresource.py
import pytest
from rest_framework.test import APIClient
from ..models import MyResource


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_create_resource(api_client):
    response = api_client.post("/api/myresources/", {"name": "test"})
    assert response.status_code == 201
    assert MyResource.objects.count() == 1
```

Run your tests:
```bash
cd backend && pytest apps/myapp/tests/test_myresource.py -v
```

---

## Adding a Celery Task

### Step 1: Create the Task

Create a file in the relevant app's `tasks/` directory (or add to an existing one):

```python
# backend/apps/myapp/tasks/my_task.py
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded


@shared_task(bind=True, soft_time_limit=300, time_limit=330, max_retries=3)
def my_task(self, resource_id):
    try:
        # Do work here
        pass
    except SoftTimeLimitExceeded:
        # Log and mark as failed, don't crash
        return {"status": "timeout"}
    except self.MaxRetriesExceededError:
        return {"status": "failed"}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)
```

### Step 2: Register the Module (if new)

If you created a **new** `tasks_*.py` file, add it to `register_extra_tasks` in `backend/config/celery.py`:

```python
# backend/config/celery.py — inside register_extra_tasks()
from apps.myapp.tasks import my_task  # noqa: F401
```

### Step 3: Add to beat_schedule (if periodic)

If the task runs on a schedule, add it to `beat_schedule` in `celery.py`:

```python
"beat_schedule": {
    ...
    "my-periodic-task": {
        "task": "apps.myapp.tasks.my_task.my_task",
        "schedule": crontab(hour=0, minute=0),  # daily at midnight
        "args": (),
    },
}
```

### Step 4: Verify

```bash
# Confirm the task is registered
cd backend && celery -A config inspect registered | grep my_task

# Test manually
python manage.py shell -c "from apps.myapp.tasks.my_task import my_task; my_task.delay('test-id')"
```

---

## Testing

### Running Tests

```bash
# Run all tests
cd backend && pytest

# Run only fast tests (skip slow, integration, e2e)
pytest -m "not slow"

# Run tests with coverage report
pytest --cov=apps

# Run a specific test file
pytest apps/myapp/tests/test_myresource.py -v

# Run a specific test
pytest apps/myapp/tests/test_myresource.py::test_create_resource -v
```

### Test Markers

Defined in `pytest.ini`:

| Marker | Description |
|--------|-------------|
| `slow` | Tests that take > 5s (external API calls, large data) |
| `integration` | Tests that need running services (DB, Redis) |
| `security` | Security-focused tests |
| `e2e` | End-to-end tests |
| `smoke` | Quick health-check tests |

### Writing Good Tests

- **Arrange, Act, Assert.** Clear structure in every test.
- **Use fixtures.** Don't repeat setup code — put it in `conftest.py`.
- **Test edge cases.** Empty inputs, boundary values, error paths.
- **One assertion per concept.** Don't test five things in one test.
- **Use `@pytest.mark.django_db`** for any test that touches the database.
- **Mock external services.** Don't call real APIs in unit tests.

### CI Expectations

All tests must pass in CI before a PR can be merged. CI runs:
1. `ruff check backend/`
2. `cd frontend && npm run lint`
3. `pytest` (full suite)
4. `pylint` (backend, blocking check)

---

## Architecture Overview

### Project Structure

```
smsly-hosting/
├── backend/                  # Django application
│   ├── apps/
│   │   ├── deployments/      # Core service management
│   │   ├── billing/          # Payment and subscription logic
│   │   ├── ai/               # AI-powered features
│   │   └── ...
│   ├── config/               # Django settings, celery, urls
│   ├── lib/                  # Shared utilities
│   └── manage.py
├── frontend/                 # Next.js application
│   ├── src/
│   │   ├── app/              # App router pages
│   │   ├── components/       # React components
│   │   └── lib/              # Utilities, API client
│   └── package.json
├── scripts/                  # Deployment and ops scripts
├── lib/                      # Shared shell libraries
└── docker-compose.yml
```

### Key Technologies

- **Backend:** Django 5.x, DRF, Celery, PostgreSQL, Redis
- **Frontend:** Next.js 15, React 18, TypeScript, Tailwind CSS
- **Infrastructure:** Docker, Docker Compose, Traefik (reverse proxy)
- **Task Queue:** Celery with Redis broker, beat scheduler

### Important Files

- `AGENTS.md` — **Read this before making any changes.** Contains lessons learned from production bugs.
- `DEVELOPER_GUIDE.md` — Detailed walkthrough of the architecture and common workflows.
- `backend/config/celery.py` — Celery configuration, task registration, beat schedule.
- `backend/config/settings/` — Django settings split by environment.
- `lib/common.sh` — Shared shell functions used by deployment scripts.

### Design Principles

1. **Everything is a Service.** Deployments, databases, cron jobs — all modeled as `Service` objects with state machines.
2. **Idempotent operations.** Scripts and tasks must be safe to re-run.
3. **Explicit state transitions.** Services move through well-defined states (e.g., `PENDING` → `BUILDING` → `RUNNING`). Never skip states.
4. **Fail loudly.** Errors should surface immediately, not be swallowed. Use structured logging.
5. **One task, one responsibility.** If a function does too many things, split it.

---

## Getting Help

- **Issues:** Search existing issues before creating a new one.
- **Discussions:** Use GitHub Discussions for architecture questions or brainstorming.
- **Code of Conduct:** Be respectful, constructive, and inclusive. We are building for the world.
