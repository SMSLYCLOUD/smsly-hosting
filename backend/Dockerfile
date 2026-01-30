FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    gcc \
    git \
    libpq-dev \
    postgresql-client \
    curl \
    wget \
    docker.io \
    && curl -sL https://nixpacks.com/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

# Note: Docker socket will be mounted from host via:
# -v /var/run/docker.sock:/var/run/docker.sock
# This is more secure than Docker-in-Docker

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use Entrypoint
ENTRYPOINT ["./entrypoint.sh"]
