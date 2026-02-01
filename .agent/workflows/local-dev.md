---
description: Build and test frontend/backend locally before deploying
---

# Local Development Workflow

## Quick Start (Development Mode)

### 1. Start infrastructure containers

// turbo

```bash
docker run -d --name postgres -p 5432:5432 -e POSTGRES_USER=smsly -e POSTGRES_PASSWORD=smsly_dev -e POSTGRES_DB=smsly_hosting postgres:16-alpine
docker run -d --name redis -p 6379:6379 redis:alpine
```

### 2. Generate .env file (if not exists)

```bash
cp .env.example .env
# Edit .env and set SECRET_KEY and FIELD_ENCRYPTION_KEY
```

### 3. Install backend dependencies

// turbo

```bash
cd backend
pip install -r requirements.txt
```

### 4. Run migrations

// turbo

```bash
python manage.py migrate
```

### 5. Create superuser

```bash
python manage.py createsuperuser
```

### 6. Start backend server

```bash
python manage.py runserver 0.0.0.0:8000
# Or with Gunicorn:
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

### 7. Install frontend dependencies

// turbo

```bash
cd frontend
npm install
```

### 8. Start frontend dev server

```bash
npm run dev
```

## Access Points

- Frontend: <http://localhost:3000>
- Backend API: <http://localhost:8000/api/>
- Admin Panel: <http://localhost:8000/admin/>
- Swagger Docs: <http://localhost:8000/api/schema/swagger/>

## Running Celery (optional, for background tasks)

```bash
cd backend
celery -A config worker -l INFO
```

## Common Issues

### CORS errors

Ensure `CORS_ALLOWED_ORIGINS` in `.env` includes `http://localhost:3000`

### Database connection refused

Check PostgreSQL container is running: `docker ps | grep postgres`

### Frontend can't reach backend

Check `NEXT_PUBLIC_API_URL` in frontend `.env.local` is `http://localhost:8000/api/v1`
