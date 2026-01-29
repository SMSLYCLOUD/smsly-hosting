FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        git \
        libpq-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for DinD or socket mounting)
RUN apt-get install -y curl \
    && curl -fsSL https://get.docker.com -o get-docker.sh \
    && sh get-docker.sh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use Entrypoint
ENTRYPOINT ["./entrypoint.sh"]
