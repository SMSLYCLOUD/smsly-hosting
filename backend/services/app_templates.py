# pylint:
"""App Templates module."""
# disable=line-too-long,too-many-instance-attributes,import-outside-toplevel
"""
SMSLY Marketplace App Templates Registry

Production-ready Docker configurations for one-click deployments.
Each template defines the exact Docker image, ports, environment variables,
and health checks needed for deployment.
"""

from dataclasses import dataclass, field


@dataclass
class AppTemplate:
    """Docker-based app template for marketplace."""
    id: str
    name: str
    description: str
    category: str
    docker_image: str
    default_port: int = 8080
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    health_check: str | None = None
    docs_url: str | None = None
    required_addons: list[str] = field(default_factory=list)  # e.g., ['POSTGRES', 'REDIS']
    logo_url: str | None = None
    website_url: str | None = None
    source_url: str | None = None
    docker_url: str | None = None
    tags: list[str] = field(default_factory=list)
    dashboard_port: int | None = None
    supports_dashboard: bool = False
    supports_public_url: bool = True
    supports_private_url: bool = True
    requires_persistence: bool = False
    post_deploy_notes: str | None = None


# ============================================================================
# COMPREHENSIVE APP TEMPLATE REGISTRY
# All Docker images are official or verified open-source images
# ============================================================================

APP_TEMPLATES: dict[str, AppTemplate] = {

    # ===== SMSLY ECOSYSTEM =====
    'smsly-platform-api': AppTemplate(
        id='smsly-platform-api',
        name='SMSLY Platform API',
        description='Core orchestration engine for the SMSLY ecosystem.',
        category='smsly-ecosystem',
        docker_image='smslycloud/platform-api:latest',
        default_port=8080,
        env_vars={
            'ENVIRONMENT': 'production',
            'SECRET_KEY': '${RANDOM_PASSWORD}',
            'DATABASE_URL': 'postgresql://...',
            'REDIS_URL': 'redis://...',
            'GATEWAY_SECRET': '${RANDOM_PASSWORD}'
        },
        health_check='curl -f http://localhost:8080/health || exit 1',
        required_addons=['POSTGRES', 'REDIS'],
    ),
    'smsly-sms': AppTemplate(
        id='smsly-sms',
        name='SMSLY SMS Gateway',
        description='High-performance SMS gateway with SMPP support.',
        category='smsly-ecosystem',
        docker_image='smslycloud/sms:latest',
        default_port=8000,
        env_vars={'ENVIRONMENT': 'production', 'PYTHONUNBUFFERED': '1'},
        health_check='curl -f http://localhost:8000/health/live || exit 1',
        required_addons=['POSTGRES', 'REDIS'],
    ),
    'smsly-voice': AppTemplate(
        id='smsly-voice',
        name='SMSLY Voice Engine',
        description='Real-time programmable voice and IVR platform.',
        category='smsly-ecosystem',
        docker_image='smslycloud/voice:latest',
        default_port=3000,
        env_vars={'RUST_LOG': 'info'},
        health_check='curl -f http://localhost:3000/health || exit 1',
    ),
    'smsly-marketing': AppTemplate(
        id='smsly-marketing',
        name='SMSLY Marketing Automation',
        description='Campaign management and customer engagement platform.',
        category='smsly-ecosystem',
        docker_image='smslycloud/marketing:latest',
        default_port=8030,
        env_vars={'DATABASE_URL': 'postgresql://...'},
        health_check='curl -f http://localhost:8030/health || exit 1',
        required_addons=['POSTGRES'],
    ),

    # ===== DATABASES =====
    'postgres': AppTemplate(
        id='postgres',
        name='PostgreSQL',
        description='The world\'s most advanced open source relational database.',
        category='database',
        docker_image='postgres:16-alpine',
        default_port=5432,
        env_vars={
            'POSTGRES_USER': 'app',
            'POSTGRES_PASSWORD': '${RANDOM_PASSWORD}',
            'POSTGRES_DB': 'app_db'},
        volumes=['/var/lib/postgresql/data'],
        health_check='pg_isready -U app',
        docs_url='https://hub.docker.com/_/postgres',
    ),
    'redis': AppTemplate(
        id='redis',
        name='Redis',
        description='In-memory data structure store, used as database, cache, and message broker.',
        category='database',
        docker_image='redis:7-alpine',
        default_port=6379,
        env_vars={},
        volumes=['/data'],
        health_check='redis-cli ping',
        docs_url='https://hub.docker.com/_/redis',
    ),
    'mongodb': AppTemplate(
        id='mongodb',
        name='MongoDB',
        description='Document database designed for ease of development and scaling.',
        category='database',
        docker_image='mongo:7',
        default_port=27017,
        env_vars={'MONGO_INITDB_ROOT_USERNAME': 'app',
                  'MONGO_INITDB_ROOT_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/data/db'],
        docs_url='https://hub.docker.com/_/mongo',
    ),
    'mysql': AppTemplate(
        id='mysql',
        name='MySQL',
        description='The most popular open-source relational database in the world.',
        category='database',
        docker_image='mysql:8.0',
        default_port=3306,
        env_vars={
            'MYSQL_ROOT_PASSWORD': '${RANDOM_PASSWORD}',
            'MYSQL_DATABASE': 'app_db'},
        volumes=['/var/lib/mysql'],
        docs_url='https://hub.docker.com/_/mysql',
    ),
    'mariadb': AppTemplate(
        id='mariadb',
        name='MariaDB',
        description='Community-developed fork of MySQL with enhanced features.',
        category='database',
        docker_image='mariadb:11',
        default_port=3306,
        env_vars={
            'MARIADB_ROOT_PASSWORD': '${RANDOM_PASSWORD}',
            'MARIADB_DATABASE': 'app_db'},
        volumes=['/var/lib/mysql'],
        docs_url='https://hub.docker.com/_/mariadb',
    ),
    'clickhouse': AppTemplate(
        id='clickhouse',
        name='ClickHouse',
        description='Fast open-source OLAP database for real-time analytics.',
        category='database',
        docker_image='clickhouse/clickhouse-server:latest',
        default_port=8123,
        volumes=['/var/lib/clickhouse'],
        docs_url='https://hub.docker.com/r/clickhouse/clickhouse-server',
    ),
    'influxdb': AppTemplate(
        id='influxdb',
        name='InfluxDB',
        description='Time series database for metrics, events, and real-time analytics.',
        category='database',
        docker_image='influxdb:2-alpine',
        default_port=8086,
        env_vars={'DOCKER_INFLUXDB_INIT_MODE': 'setup', 'DOCKER_INFLUXDB_INIT_USERNAME': 'admin',
                  'DOCKER_INFLUXDB_INIT_PASSWORD': '${RANDOM_PASSWORD}', 'DOCKER_INFLUXDB_INIT_ORG': 'smsly',
                  'DOCKER_INFLUXDB_INIT_BUCKET': 'default'},
        volumes=['/var/lib/influxdb2'],
        docs_url='https://hub.docker.com/_/influxdb',
    ),
    'elasticsearch': AppTemplate(
        id='elasticsearch',
        name='Elasticsearch',
        description='Distributed search and analytics engine for all types of data.',
        category='database',
        docker_image='docker.elastic.co/elasticsearch/elasticsearch:8.12.0',
        default_port=9200,
        env_vars={
            'discovery.type': 'single-node',
            'xpack.security.enabled': 'false'},
        volumes=['/usr/share/elasticsearch/data'],
        docs_url='https://www.elastic.co/guide/en/elasticsearch/reference/current/docker.html',
    ),
    'meilisearch': AppTemplate(
        id='meilisearch',
        name='Meilisearch',
        description='Lightning-fast, open-source search engine alternative to Algolia.',
        category='database',
        docker_image='getmeili/meilisearch:v1.6',
        default_port=7700,
        env_vars={'MEILI_MASTER_KEY': '${RANDOM_PASSWORD}'},
        volumes=['/meili_data'],
        docs_url='https://hub.docker.com/r/getmeili/meilisearch',
    ),
    'neo4j': AppTemplate(
        id='neo4j',
        name='Neo4j',
        description='Graph database platform for connected data applications.',
        category='database',
        docker_image='neo4j:5-community',
        default_port=7474,
        env_vars={'NEO4J_AUTH': 'neo4j/${RANDOM_PASSWORD}'},
        volumes=['/data', '/logs'],
        docs_url='https://hub.docker.com/_/neo4j',
    ),
    'cassandra': AppTemplate(
        id='cassandra',
        name='Cassandra',
        description='Highly-scalable partitioned row store NoSQL database.',
        category='database',
        docker_image='cassandra:4',
        default_port=9042,
        volumes=['/var/lib/cassandra'],
        docs_url='https://hub.docker.com/_/cassandra',
    ),
    'supabase': AppTemplate(
        id='supabase',
        name='Supabase (Postgres)',
        description='Open-source Firebase alternative with Postgres database.',
        category='database',
        docker_image='supabase/postgres:15.1.0.147',
        default_port=5432,
        env_vars={'POSTGRES_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/var/lib/postgresql/data'],
        docs_url='https://hub.docker.com/r/supabase/postgres',
    ),

    # ===== CMS & WEBSITES =====
    'wordpress': AppTemplate(
        id='wordpress',
        name='WordPress',
        description='The most popular CMS for building websites and blogs.',
        category='cms',
        docker_image='wordpress:6-apache',
        default_port=80,
        env_vars={'WORDPRESS_DB_HOST': 'db:3306', 'WORDPRESS_DB_USER': 'wp',
                  'WORDPRESS_DB_PASSWORD': '${RANDOM_PASSWORD}', 'WORDPRESS_DB_NAME': 'wordpress'},
        volumes=['/var/www/html'],
        required_addons=['MYSQL'],
        docs_url='https://hub.docker.com/_/wordpress',
    ),
    'ghost': AppTemplate(
        id='ghost',
        name='Ghost',
        description='Professional publishing platform for modern online publications.',
        category='cms',
        docker_image='ghost:5-alpine',
        default_port=2368,
        env_vars={'url': 'https://${DOMAIN}', 'database__client': 'sqlite3'},
        volumes=['/var/lib/ghost/content'],
        docs_url='https://hub.docker.com/_/ghost',
    ),
    'strapi': AppTemplate(
        id='strapi',
        name='Strapi',
        description='Leading open-source headless CMS with customizable API.',
        category='cms',
        docker_image='strapi/strapi:4',
        default_port=1337,
        env_vars={
            'DATABASE_CLIENT': 'sqlite',
            'DATABASE_FILENAME': '.tmp/data.db'},
        volumes=['/srv/app'],
        docs_url='https://hub.docker.com/r/strapi/strapi',
    ),
    'directus': AppTemplate(
        id='directus',
        name='Directus',
        description='Open-source headless CMS and API for any SQL database.',
        category='cms',
        docker_image='directus/directus:10',
        default_port=8055,
        env_vars={'KEY': '${RANDOM_PASSWORD}', 'SECRET': '${RANDOM_PASSWORD}',
                  'ADMIN_EMAIL': 'admin@example.com', 'ADMIN_PASSWORD': '${RANDOM_PASSWORD}',
                  'DB_CLIENT': 'sqlite3', 'DB_FILENAME': '/directus/database/data.db'},
        volumes=['/directus/database', '/directus/uploads'],
        docs_url='https://hub.docker.com/r/directus/directus',
    ),

    # ===== DEV TOOLS =====
    'n8n': AppTemplate(
        id='n8n',
        name='n8n',
        description='Fair-code workflow automation tool for technical people.',
        category='dev-tools',
        docker_image='n8nio/n8n:latest',
        default_port=5678,
        env_vars={'N8N_BASIC_AUTH_ACTIVE': 'true', 'N8N_BASIC_AUTH_USER': 'admin',
                  'N8N_BASIC_AUTH_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/home/node/.n8n'],
        docs_url='https://hub.docker.com/r/n8nio/n8n',
    ),
    'gitea': AppTemplate(
        id='gitea',
        name='Gitea',
        description='Lightweight self-hosted Git service written in Go.',
        category='dev-tools',
        docker_image='gitea/gitea:latest',
        default_port=3000,
        env_vars={'USER_UID': '1000', 'USER_GID': '1000'},
        volumes=['/data', '/etc/timezone:/etc/timezone:ro'],
        docs_url='https://hub.docker.com/r/gitea/gitea',
    ),
    'gitlab': AppTemplate(
        id='gitlab',
        name='GitLab Community Edition',
        description='Complete DevOps platform with Git, CI/CD, and more.',
        category='dev-tools',
        docker_image='gitlab/gitlab-ce:latest',
        default_port=80,
        env_vars={'GITLAB_OMNIBUS_CONFIG': "external_url 'https://${DOMAIN}'"},
        volumes=['/etc/gitlab', '/var/log/gitlab', '/var/opt/gitlab'],
        docs_url='https://hub.docker.com/r/gitlab/gitlab-ce',
    ),
    'jenkins': AppTemplate(
        id='jenkins',
        name='Jenkins',
        description='The leading open source automation server for CI/CD.',
        category='dev-tools',
        docker_image='jenkins/jenkins:lts-jdk17',
        default_port=8080,
        volumes=['/var/jenkins_home'],
        docs_url='https://hub.docker.com/r/jenkins/jenkins',
    ),
    'drone': AppTemplate(
        id='drone',
        name='Drone CI',
        description='Container-native CI/CD platform with simple YAML config.',
        category='dev-tools',
        docker_image='drone/drone:2',
        default_port=80,
        env_vars={'DRONE_SERVER_HOST': '${DOMAIN}', 'DRONE_SERVER_PROTO': 'https',
                  'DRONE_RPC_SECRET': '${RANDOM_PASSWORD}'},
        volumes=['/data'],
        docs_url='https://hub.docker.com/r/drone/drone',
    ),
    'sonarqube': AppTemplate(
        id='sonarqube',
        name='SonarQube',
        description='Code quality and security scanning platform.',
        category='dev-tools',
        docker_image='sonarqube:community',
        default_port=9000,
        volumes=[
            '/opt/sonarqube/data',
            '/opt/sonarqube/logs',
            '/opt/sonarqube/extensions'],
        docs_url='https://hub.docker.com/_/sonarqube',
    ),
    'harbor': AppTemplate(
        id='harbor',
        name='Harbor Registry',
        description='Enterprise-class container registry with security features.',
        category='dev-tools',
        docker_image='goharbor/harbor-core:v2.10.0',
        default_port=8080,
        docs_url='https://goharbor.io/docs/',
    ),
    'vault': AppTemplate(
        id='vault',
        name='HashiCorp Vault',
        description='Secrets management and data protection platform.',
        category='dev-tools',
        docker_image='hashicorp/vault:1.15',
        default_port=8200,
        env_vars={'VAULT_DEV_ROOT_TOKEN_ID': '${RANDOM_PASSWORD}',
                  'VAULT_DEV_LISTEN_ADDRESS': '0.0.0.0:8200'},
        volumes=['/vault/data'],
        docs_url='https://hub.docker.com/r/hashicorp/vault',
    ),
    'minio': AppTemplate(
        id='minio',
        name='MinIO',
        description='High-performance S3-compatible object storage.',
        category='dev-tools',
        docker_image='minio/minio:latest',
        default_port=9000,
        env_vars={'MINIO_ROOT_USER': 'admin',
                  'MINIO_ROOT_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/data'],
        docs_url='https://hub.docker.com/r/minio/minio',
    ),
    'registry': AppTemplate(
        id='registry',
        name='Docker Registry',
        description='Private container image registry for Docker images.',
        category='dev-tools',
        docker_image='registry:2',
        default_port=5000,
        volumes=['/var/lib/registry'],
        docs_url='https://hub.docker.com/_/registry',
    ),
    'portainer': AppTemplate(
        id='portainer',
        name='Portainer',
        description='Container management UI for Docker, Swarm, and Kubernetes.',
        category='dev-tools',
        docker_image='portainer/portainer-ce:latest',
        default_port=9443,
        volumes=['/data', '/var/run/docker.sock:/var/run/docker.sock'],
        docs_url='https://hub.docker.com/r/portainer/portainer-ce',
    ),

    # ===== ANALYTICS & MONITORING =====
    'metabase': AppTemplate(
        id='metabase',
        name='Metabase',
        description='The simplest way to get business intelligence and analytics.',
        category='analytics',
        docker_image='metabase/metabase:latest',
        default_port=3000,
        env_vars={'MB_DB_TYPE': 'h2'},
        volumes=['/metabase-data'],
        docs_url='https://hub.docker.com/r/metabase/metabase',
    ),
    'grafana': AppTemplate(
        id='grafana',
        name='Grafana',
        description='Observability and data visualization platform.',
        category='analytics',
        docker_image='grafana/grafana-oss:latest',
        default_port=3000,
        env_vars={'GF_SECURITY_ADMIN_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/var/lib/grafana'],
        docs_url='https://hub.docker.com/r/grafana/grafana-oss',
    ),
    'prometheus': AppTemplate(
        id='prometheus',
        name='Prometheus',
        description='Systems monitoring and alerting toolkit.',
        category='analytics',
        docker_image='prom/prometheus:latest',
        default_port=9090,
        volumes=['/prometheus'],
        docs_url='https://hub.docker.com/r/prom/prometheus',
    ),
    'superset': AppTemplate(
        id='superset',
        name='Apache Superset',
        description='Modern data exploration and visualization platform.',
        category='analytics',
        docker_image='apache/superset:latest',
        default_port=8088,
        env_vars={'SUPERSET_SECRET_KEY': '${RANDOM_PASSWORD}'},
        docs_url='https://hub.docker.com/r/apache/superset',
    ),
    'redash': AppTemplate(
        id='redash',
        name='Redash',
        description='Connect and visualize all your data sources.',
        category='analytics',
        docker_image='redash/redash:latest',
        default_port=5000,
        env_vars={'REDASH_SECRET_KEY': '${RANDOM_PASSWORD}'},
        required_addons=['POSTGRES', 'REDIS'],
        docs_url='https://hub.docker.com/r/redash/redash',
    ),
    'umami': AppTemplate(
        id='umami',
        name='Umami',
        description='Simple, fast, privacy-focused alternative to Google Analytics.',
        category='analytics',
        docker_image='ghcr.io/umami-software/umami:postgresql-latest',
        default_port=3000,
        env_vars={'DATABASE_URL': 'postgresql://...'},
        required_addons=['POSTGRES'],
        docs_url='https://umami.is/docs',
    ),
    'plausible': AppTemplate(
        id='plausible',
        name='Plausible Analytics',
        description='Lightweight privacy-friendly Google Analytics alternative.',
        category='analytics',
        docker_image='plausible/analytics:latest',
        default_port=8000,
        env_vars={'BASE_URL': 'https://${DOMAIN}',
                  'SECRET_KEY_BASE': '${RANDOM_PASSWORD}'},
        required_addons=['POSTGRES'],
        docs_url='https://hub.docker.com/r/plausible/analytics',
    ),
    'matomo': AppTemplate(
        id='matomo',
        name='Matomo',
        description='Privacy-respecting web analytics platform.',
        category='analytics',
        docker_image='matomo:fpm-alpine',
        default_port=80,
        volumes=['/var/www/html'],
        required_addons=['MYSQL'],
        docs_url='https://hub.docker.com/_/matomo',
    ),
    'uptime-kuma': AppTemplate(
        id='uptime-kuma',
        name='Uptime Kuma',
        description='Self-hosted uptime monitoring tool with beautiful UI.',
        category='analytics',
        docker_image='louislam/uptime-kuma:1',
        default_port=3001,
        volumes=['/app/data'],
        docs_url='https://hub.docker.com/r/louislam/uptime-kuma',
    ),

    # ===== COMMUNICATION =====
    'mattermost': AppTemplate(
        id='mattermost',
        name='Mattermost',
        description='Open-source Slack alternative for secure team collaboration.',
        category='cms',
        docker_image='mattermost/mattermost-team-edition:latest',
        default_port=8065,
        volumes=[
            '/mattermost/config',
            '/mattermost/data',
            '/mattermost/logs',
            '/mattermost/plugins'],
        required_addons=['POSTGRES'],
        docs_url='https://hub.docker.com/r/mattermost/mattermost-team-edition',
    ),
    'rocketchat': AppTemplate(
        id='rocketchat',
        name='Rocket.Chat',
        description='Open-source team communication platform.',
        category='cms',
        docker_image='rocket.chat:latest',
        default_port=3000,
        env_vars={
            'ROOT_URL': 'https://${DOMAIN}',
            'MONGO_URL': 'mongodb://...'},
        volumes=['/app/uploads'],
        required_addons=['MONGODB'],
        docs_url='https://hub.docker.com/_/rocket.chat',
    ),
    'jitsi': AppTemplate(
        id='jitsi',
        name='Jitsi Meet',
        description='Secure, flexible, and open-source video conferencing.',
        category='cms',
        docker_image='jitsi/web:stable',
        default_port=443,
        env_vars={'PUBLIC_URL': 'https://${DOMAIN}'},
        docs_url='https://hub.docker.com/u/jitsi',
    ),

    # ===== PROJECT MANAGEMENT & DOCS =====
    'nextcloud': AppTemplate(
        id='nextcloud',
        name='Nextcloud',
        description='Self-hosted productivity platform (files, calendar, contacts).',
        category='cms',
        docker_image='nextcloud:28-apache',
        default_port=80,
        env_vars={'NEXTCLOUD_ADMIN_USER': 'admin',
                  'NEXTCLOUD_ADMIN_PASSWORD': '${RANDOM_PASSWORD}'},
        volumes=['/var/www/html'],
        docs_url='https://hub.docker.com/_/nextcloud',
    ),
    'outline': AppTemplate(
        id='outline',
        name='Outline',
        description='Modern team knowledge base and wiki.',
        category='cms',
        docker_image='outlinewiki/outline:latest',
        default_port=3000,
        env_vars={
            'SECRET_KEY': '${RANDOM_PASSWORD}',
            'UTILS_SECRET': '${RANDOM_PASSWORD}'},
        required_addons=['POSTGRES', 'REDIS'],
        docs_url='https://hub.docker.com/r/outlinewiki/outline',
    ),
    'bookstack': AppTemplate(
        id='bookstack',
        name='BookStack',
        description='Simple, self-hosted wiki/documentation platform.',
        category='cms',
        docker_image='lscr.io/linuxserver/bookstack:latest',
        default_port=80,
        env_vars={
            'APP_URL': 'https://${DOMAIN}',
            'DB_HOST': 'db',
            'DB_DATABASE': 'bookstack'},
        volumes=['/config'],
        required_addons=['MYSQL'],
        docs_url='https://hub.docker.com/r/linuxserver/bookstack',
    ),
    'plane': AppTemplate(
        id='plane',
        name='Plane',
        description='Open-source project management alternative to Jira.',
        category='dev-tools',
        docker_image='makeplane/plane-frontend:latest',
        default_port=3000,
        required_addons=['POSTGRES', 'REDIS'],
        docs_url='https://hub.docker.com/r/makeplane/plane-frontend',
    ),
    'focalboard': AppTemplate(
        id='focalboard',
        name='Focalboard',
        description='Open-source project management alternative to Notion/Trello.',
        category='dev-tools',
        docker_image='mattermost/focalboard:latest',
        default_port=8000,
        volumes=['/opt/focalboard/data'],
        docs_url='https://hub.docker.com/r/mattermost/focalboard',
    ),

    # ===== SHARED AI INFRASTRUCTURE =====
    'ollama-cpp': AppTemplate(
        id='ollama-cpp',
        name='Ollama CPP (Shared LLM Server)',
        description='Optimised Ollama C++ runtime shared across all LLM models. Auto-managed — deploys once per project and serves all LLM services. Saves VPS resources by running a single inference engine instead of one per model.',
        category='ai-infra',
        docker_image='ollama/ollama:latest',
        default_port=11434,
        env_vars={
            'OLLAMA_HOST': '0.0.0.0',
            'OLLAMA_KEEP_ALIVE': '24h',
        },
        volumes=['/root/.ollama'],
        health_check='curl -f http://localhost:11434/api/tags || exit 1',
        supports_public_url=True,
        supports_dashboard=False,
        post_deploy_notes='Shared Ollama runtime. All LLM models will pull into this single instance. Do NOT delete manually — it is auto-managed.',
    ),

    # ===== BACKEND PLATFORMS =====
    'appwrite': AppTemplate(
        id='appwrite',
        name='Appwrite',
        description='Secure backend platform for web, mobile, and Flutter apps.',
        category='dev-tools',
        docker_image='appwrite/appwrite:1.5',
        default_port=80,
        env_vars={'_APP_ENV': 'production'},
        volumes=['/storage'],
        docs_url='https://hub.docker.com/r/appwrite/appwrite',
    ),
    'pocketbase': AppTemplate(
        id='pocketbase',
        name='PocketBase',
        description='Open-source backend in a single file (Go + SQLite).',
        category='dev-tools',
        docker_image='ghcr.io/muchobien/pocketbase:latest',
        default_port=8090,
        volumes=['/pb_data'],
        docs_url='https://pocketbase.io/',
    ),
    'nocodb': AppTemplate(
        id='nocodb',
        name='NocoDB',
        description='Open-source Airtable alternative, turns any database into a spreadsheet.',
        category='dev-tools',
        docker_image='nocodb/nocodb:latest',
        default_port=8080,
        env_vars={'NC_DB': 'sqlite3:///usr/app/data/noco.db'},
        volumes=['/usr/app/data'],
        docs_url='https://hub.docker.com/r/nocodb/nocodb',
    ),
}


def get_template(template_id: str) -> AppTemplate:
    """Get app template by ID."""
    return APP_TEMPLATES.get(template_id)  # type: ignore[return-value]


def list_templates(category: str | None = None) -> list:
    """List all templates, optionally filtered by category."""
    templates = list(APP_TEMPLATES.values())
    if category:
        templates = [t for t in templates if t.category == category]
    return templates


def get_docker_run_command(
        template_id: str, name: str | None = None, domain: str | None = None) -> str:
    """Generate docker run command for a template."""
    import secrets

    template = get_template(template_id)
    if not template:
        return ""

    name = name or f"{template_id}-{secrets.token_hex(4)}"

    cmd_parts = [
        'docker',
        'run',
        '-d',
        '--name',
        name,
        '--restart',
        'unless-stopped']

    # Port mapping
    cmd_parts.extend(
        ['-p', f'{template.default_port}:{template.default_port}'])

    # Environment variables
    for key, value in template.env_vars.items():
        # Replace placeholders
        if '${RANDOM_PASSWORD}' in value:
            value = value.replace(
                '${RANDOM_PASSWORD}',
                secrets.token_urlsafe(24))
        if domain and '${DOMAIN}' in value:
            value = value.replace('${DOMAIN}', domain)
        cmd_parts.extend(['-e', f'{key}={value}'])

    # Volumes
    for vol in template.volumes:
        if ':' in vol:  # Already has host path
            cmd_parts.extend(['-v', vol])
        else:  # Container path only, create named volume
            vol_name = f"{name}-{vol.replace('/', '-').strip('-')}"
            cmd_parts.extend(['-v', f'{vol_name}:{vol}'])

    # Image
    cmd_parts.append(template.docker_image)

    return ' '.join(cmd_parts)
