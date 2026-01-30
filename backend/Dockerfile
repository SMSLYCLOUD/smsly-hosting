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
        curl \
        wget \
    && curl -fsSL https://get.docker.com -o get-docker.sh \
    && sh get-docker.sh \
    # Install Nixpacks
    && curl -sL https://nixpacks.com/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use Entrypoint
ENTRYPOINT ["./entrypoint.sh"]
