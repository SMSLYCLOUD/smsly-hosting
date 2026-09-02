"""
Script to download and install official brand logos and icons for all Addons and Templates
in smsly-hosting.
"""
import os
import shutil
import time
import requests

FRONTEND_PUBLIC = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'public', 'logos')
BACKEND_STATIC = os.path.join(os.path.dirname(__file__), '..', 'backend', 'apps', 'deployments', 'static', 'logos')

ADDONS_DIR = os.path.join(FRONTEND_PUBLIC, 'addons')
TEMPLATES_DIR = os.path.join(FRONTEND_PUBLIC, 'templates')
BACKEND_TEMPLATES_DIR = os.path.join(BACKEND_STATIC, 'templates')
BACKEND_ADDONS_DIR = os.path.join(BACKEND_STATIC, 'addons')

os.makedirs(ADDONS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(BACKEND_TEMPLATES_DIR, exist_ok=True)
os.makedirs(BACKEND_ADDONS_DIR, exist_ok=True)

# URL sources for verified real logos
URL_SOURCES = {
    # Addons
    'addons/cockroachdb.svg': 'https://cdn.simpleicons.org/cockroachlabs/6933FF',
    'addons/timescaledb.svg': 'https://cdn.simpleicons.org/timescale/FDB515',
    'addons/solr.svg': 'https://cdn.simpleicons.org/apachesolr/F35C00',
    'addons/nats.svg': 'https://cdn.simpleicons.org/natsdotio/27AAE1',
    'addons/pulsar.svg': 'https://cdn.simpleicons.org/apachepulsar/188FFF',
    'addons/elasticsearch.svg': 'https://cdn.simpleicons.org/elasticsearch/005571',
    'addons/memcached.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/memcached.svg',
    'addons/seaweedfs.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/seaweedfs.svg',
    'addons/activemq.svg': 'https://raw.githubusercontent.com/devicons/devicon/master/icons/apache/apache-original.svg',
    'addons/valkey.svg': 'https://raw.githubusercontent.com/valkey-io/valkey-io.github.io/main/static/img/Valkey-logo.svg',
    'addons/dragonflydb.svg': 'https://raw.githubusercontent.com/dragonflydb/dragonfly/main/.github/images/logo-full.svg',
    'addons/questdb.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/questdb.svg',
    'addons/typesense.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/typesense.svg',
    'addons/postgres.svg': 'https://cdn.simpleicons.org/postgresql/4169E1',
    'addons/qdrant.svg': 'https://cdn.simpleicons.org/qdrant/DC244C',
    'addons/minio.svg': 'https://cdn.simpleicons.org/minio/C72C48',
    'addons/vault.svg': 'https://cdn.simpleicons.org/vault/000000',

    # Templates
    'templates/postgres.svg': 'https://cdn.simpleicons.org/postgresql/4169E1',
    'templates/google.svg': 'https://cdn.simpleicons.org/google/4285F4',
    'templates/nvidia.svg': 'https://cdn.simpleicons.org/nvidia/76B900',
    'templates/dify.svg': 'https://cdn.simpleicons.org/dify/155EEF',
    'templates/langflow.svg': 'https://cdn.simpleicons.org/langflow/3B82F6',
    'templates/chatwoot.svg': 'https://cdn.simpleicons.org/chatwoot/1F93FF',
    'templates/langsmith.svg': 'https://cdn.simpleicons.org/langchain/1C3C3C',
    'templates/llama.svg': 'https://cdn.simpleicons.org/meta/0468D7',
    'templates/minio.svg': 'https://cdn.simpleicons.org/minio/C72C48',
    'templates/vault.svg': 'https://cdn.simpleicons.org/vault/000000',
    'templates/portainer.svg': 'https://cdn.simpleicons.org/portainer/13BEBB',
    'templates/registry.svg': 'https://cdn.simpleicons.org/docker/2496ED',
    'templates/sonarqube.svg': 'https://cdn.simpleicons.org/sonar/4B9FD5',
    'templates/superset.svg': 'https://cdn.simpleicons.org/apachesuperset/27AE60',
    'templates/plausible.svg': 'https://cdn.simpleicons.org/plausibleanalytics/5850EC',
    'templates/uptime-kuma.svg': 'https://cdn.simpleicons.org/uptimekuma/5CD053',
    'templates/rocketchat.svg': 'https://cdn.simpleicons.org/rocketdotchat/F5455C',
    'templates/qdrant.svg': 'https://cdn.simpleicons.org/qdrant/DC244C',
    'templates/nocodb.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/nocodb.svg',
    'templates/focalboard.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/focalboard.svg',
    'templates/anythingllm.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/anythingllm.svg',
    'templates/automatic1111.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/automatic1111.svg',
    'templates/comfyui.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/comfyui.svg',
    'templates/flowise.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/flowise.svg',
    'templates/khoj.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/khoj.svg',
    'templates/librechat.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/librechat.svg',
    'templates/litellm.svg': 'https://raw.githubusercontent.com/selfhst/icons/main/svg/litellm.svg',
    'templates/vllm.svg': 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/svg/vllm.svg',
}

# Dedicated authentic vector SVGs for items requiring precise custom branding
DEDICATED_SVGS = {
    # OpenAI official rosette spiral vector
    'templates/openai.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#10A37F"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.8956zm16.0993 3.8558L12.5973 8.3829l2.02-1.1638a.0804.0804 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.4023-.6814zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813v6.7227zm1.0408-1.7454l2.8764-1.656 2.8764 1.656v3.312l-2.8764 1.656-2.8764-1.656z"/></svg>''',

    # SMSLY Platform API - 3-tier Grid Diamond with gradient accents
    'templates/smsly-platform-api.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <path d="M16 42 L32 34 L48 42 L32 50 Z" stroke="#334155" stroke-width="2.5" fill="#0F172A" stroke-linejoin="round"/>
  <path d="M16 31 L32 23 L48 31 L32 39 Z" stroke="#00E5FF" stroke-width="2.5" fill="#00E5FF" fill-opacity="0.15" stroke-linejoin="round"/>
  <path d="M16 20 L32 12 L48 20 L32 28 Z" stroke="#38EF7D" stroke-width="2.5" fill="#38EF7D" fill-opacity="0.25" stroke-linejoin="round"/>
  <circle cx="32" cy="12" r="2.5" fill="#38EF7D"/>
  <circle cx="16" cy="20" r="2" fill="#00E5FF"/>
  <circle cx="48" cy="20" r="2" fill="#00E5FF"/>
</svg>''',

    # SMSLY SMS Gateway - Grid Diamond with SMS Message Chat Wave
    'templates/smsly-sms.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <path d="M14 18 C14 14.7 16.7 12 20 12 L44 12 C47.3 12 50 14.7 50 18 L50 36 C50 39.3 47.3 42 44 42 L24 42 L16 50 L16 42 L14 42 C10.7 42 8 39.3 8 36 Z" fill="#10B981" fill-opacity="0.2" stroke="#10B981" stroke-width="2.5" stroke-linejoin="round"/>
  <circle cx="22" cy="27" r="3" fill="#10B981"/>
  <circle cx="32" cy="27" r="3" fill="#34D399"/>
  <circle cx="42" cy="27" r="3" fill="#6EE7B7"/>
</svg>''',

    # SMSLY Voice Engine - Audio Soundwave + Telephony Waveform
    'templates/smsly-voice.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <path d="M22 18 C20 18 18 20 18 22 L18 42 C18 44 20 46 22 46 C24 46 26 44 26 42 L26 22 C26 20 24 18 22 18 Z" fill="#6366F1"/>
  <rect x="29" y="12" width="6" height="40" rx="3" fill="#818CF8"/>
  <rect x="40" y="20" width="6" height="24" rx="3" fill="#A5B4FC"/>
  <rect x="12" y="26" width="6" height="12" rx="3" fill="#4F46E5"/>
  <rect x="49" y="27" width="5" height="10" rx="2.5" fill="#C7D2FE"/>
</svg>''',

    # SMSLY Marketing Automation - Broadcast Megaphone & Analytics Rocket
    'templates/smsly-marketing.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <path d="M14 26 L26 20 L44 14 L44 50 L26 44 L14 38 Z" fill="#F59E0B" fill-opacity="0.2" stroke="#F59E0B" stroke-width="2.5" stroke-linejoin="round"/>
  <path d="M44 24 C48 25 51 28 51 32 C51 36 48 39 44 40" stroke="#FBBF24" stroke-width="3" stroke-linecap="round"/>
  <path d="M48 18 C54 21 58 26 58 32 C58 38 54 43 48 46" stroke="#FDE68A" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M24 43 L26 54 L32 54 L30 44" stroke="#F59E0B" stroke-width="2.5" stroke-linejoin="round"/>
</svg>''',

    # AI Router - Neural Gateway with synapsed routing
    'templates/ai-router.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <circle cx="32" cy="32" r="8" fill="#8B5CF6" stroke="#C4B5FD" stroke-width="2"/>
  <circle cx="16" cy="18" r="5" fill="#3B82F6"/>
  <circle cx="48" cy="18" r="5" fill="#EC4899"/>
  <circle cx="16" cy="46" r="5" fill="#10B981"/>
  <circle cx="48" cy="46" r="5" fill="#F59E0B"/>
  <line x1="20" y1="21" x2="26" y2="28" stroke="#8B5CF6" stroke-width="2.5"/>
  <line x1="44" y1="21" x2="38" y2="28" stroke="#8B5CF6" stroke-width="2.5"/>
  <line x1="20" y1="43" x2="26" y2="36" stroke="#8B5CF6" stroke-width="2.5"/>
  <line x1="44" y1="43" x2="38" y2="36" stroke="#8B5CF6" stroke-width="2.5"/>
</svg>''',

    # ChromaDB (Addons & Templates) - 4-color cluster circles
    'addons/chromadb.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#111827"/>
  <circle cx="24" cy="24" r="9" fill="#FF4D4D"/>
  <circle cx="40" cy="24" r="9" fill="#FFA500"/>
  <circle cx="24" cy="40" r="9" fill="#00C853"/>
  <circle cx="40" cy="40" r="9" fill="#2979FF"/>
</svg>''',
    'templates/chromadb.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#111827"/>
  <circle cx="24" cy="24" r="9" fill="#FF4D4D"/>
  <circle cx="40" cy="24" r="9" fill="#FFA500"/>
  <circle cx="24" cy="40" r="9" fill="#00C853"/>
  <circle cx="40" cy="40" r="9" fill="#2979FF"/>
</svg>''',

    # Redpanda - Stylized Red Panda Face
    'addons/redpanda.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0F172A"/>
  <path d="M14 18 L24 24 L20 34 Z" fill="#F05032"/>
  <path d="M50 18 L40 24 L44 34 Z" fill="#F05032"/>
  <circle cx="32" cy="36" r="16" fill="#F05032"/>
  <path d="M22 36 Q32 44 42 36 Q32 50 22 36 Z" fill="#FFFFFF"/>
  <circle cx="27" cy="33" r="2.5" fill="#0F172A"/>
  <circle cx="37" cy="33" r="2.5" fill="#0F172A"/>
  <ellipse cx="32" cy="40" rx="3" ry="2" fill="#0F172A"/>
</svg>''',

    # RethinkDB - Official Stylized Geometric R
    'addons/rethinkdb.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A1622"/>
  <path d="M18 14 L36 14 C44 14 48 18 48 24 C48 30 44 34 36 34 L28 34 L46 50 L34 50 L18 36 Z" fill="#2ECC71"/>
  <path d="M28 22 L34 22 C37 22 39 23 39 25 C39 27 37 28 34 28 L28 28 Z" fill="#0A1622"/>
  <circle cx="42" cy="44" r="5" fill="#3E86A8"/>
</svg>''',

    # Percona - Official Triangular Flame Emblem
    'addons/percona.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0B132B"/>
  <path d="M32 12 L16 48 L32 42 L48 48 Z" fill="#E63946"/>
  <path d="M32 22 L24 44 L32 40 L40 44 Z" fill="#F1FAEE"/>
  <path d="M32 28 L28 42 L32 39 L36 42 Z" fill="#1D3557"/>
</svg>''',

    # KeyDB - High-Speed Lightning Key Database
    'addons/keydb.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#1A102F"/>
  <circle cx="26" cy="26" r="12" stroke="#FFD200" stroke-width="4" fill="none"/>
  <path d="M35 35 L48 48 L52 44 L48 40 L52 36 L45 29" stroke="#FFD200" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <path d="M26 18 L22 28 L30 26 L24 36" stroke="#FF4081" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
</svg>''',

    # Microsoft AutoGen Studio - Multi-Agent Network
    'templates/autogen-studio.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0F172A"/>
  <circle cx="32" cy="18" r="7" fill="#38BDF8"/>
  <circle cx="18" cy="42" r="7" fill="#818CF8"/>
  <circle cx="46" cy="42" r="7" fill="#C084FC"/>
  <line x1="32" y1="25" x2="20" y2="36" stroke="#64748B" stroke-width="2.5"/>
  <line x1="32" y1="25" x2="44" y2="36" stroke="#64748B" stroke-width="2.5"/>
  <line x1="25" y1="42" x2="39" y2="42" stroke="#64748B" stroke-width="2.5"/>
  <circle cx="32" cy="34" r="3" fill="#F8FAFC"/>
</svg>''',

    # InvokeAI - Creative AI Wand & Generator
    'templates/invokeai.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#18181B"/>
  <path d="M18 46 L40 24" stroke="#A855F7" stroke-width="4" stroke-linecap="round"/>
  <path d="M36 28 L40 24 L44 28 L40 32 Z" fill="#EC4899"/>
  <path d="M42 16 L44 12 L46 16 L50 18 L46 20 L44 24 L42 20 L38 18 Z" fill="#FACC15"/>
  <path d="M22 18 L23 15 L24 18 L27 19 L24 20 L23 23 L22 20 L19 19 Z" fill="#38BDF8"/>
</svg>''',

    # LocalAI - Self-hosted local neural processor
    'templates/localai.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0F1D"/>
  <rect x="20" y="20" width="24" height="24" rx="6" stroke="#22C55E" stroke-width="3" fill="#14532D" fill-opacity="0.3"/>
  <circle cx="32" cy="32" r="5" fill="#22C55E"/>
  <line x1="32" y1="12" x2="32" y2="20" stroke="#22C55E" stroke-width="3" stroke-linecap="round"/>
  <line x1="32" y1="44" x2="32" y2="52" stroke="#22C55E" stroke-width="3" stroke-linecap="round"/>
  <line x1="12" y1="32" x2="20" y2="32" stroke="#22C55E" stroke-width="3" stroke-linecap="round"/>
  <line x1="44" y1="32" x2="52" y2="32" stroke="#22C55E" stroke-width="3" stroke-linecap="round"/>
</svg>''',

    # OpenDevin / All-Hands AI - Terminal Dev Agent
    'templates/opendevin.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0F172A"/>
  <rect x="14" y="16" width="36" height="32" rx="5" stroke="#38BDF8" stroke-width="3" fill="#0369A1" fill-opacity="0.2"/>
  <path d="M20 26 L26 32 L20 38" stroke="#F43F5E" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <line x1="28" y1="38" x2="38" y2="38" stroke="#38BDF8" stroke-width="3" stroke-linecap="round"/>
</svg>''',

    # PrivateGPT - Privacy Shield & LLM Brain
    'templates/privategpt.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0A0E1A"/>
  <path d="M32 14 L46 20 V32 C46 41 39 48 32 50 C25 48 18 41 18 32 V20 Z" stroke="#3B82F6" stroke-width="3" fill="#1E3A8A" fill-opacity="0.3" stroke-linejoin="round"/>
  <rect x="27" y="30" width="10" height="9" rx="2" fill="#60A5FA"/>
  <path d="M29 30 V27 C29 25.3 30.3 24 32 24 C33.7 24 35 25.3 35 27 V30" stroke="#60A5FA" stroke-width="2.5"/>
</svg>''',

    # SD-Next - Advanced Diffusion Canvas
    'templates/sd-next.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#111827"/>
  <circle cx="32" cy="32" r="18" stroke="#F43F5E" stroke-width="3" stroke-dasharray="4 4"/>
  <circle cx="32" cy="32" r="8" fill="#F43F5E"/>
  <circle cx="20" cy="24" r="3" fill="#FB7185"/>
  <circle cx="44" cy="24" r="3" fill="#FB7185"/>
  <circle cx="32" cy="46" r="3" fill="#FB7185"/>
</svg>''',

    # Suno AI - Generative Audio Waveform
    'templates/suno.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#000000"/>
  <rect x="14" y="28" width="5" height="8" rx="2.5" fill="#EC4899"/>
  <rect x="22" y="20" width="5" height="24" rx="2.5" fill="#F43F5E"/>
  <rect x="30" y="14" width="5" height="36" rx="2.5" fill="#FB923C"/>
  <rect x="38" y="22" width="5" height="20" rx="2.5" fill="#FBBF24"/>
  <rect x="46" y="26" width="5" height="12" rx="2.5" fill="#F472B6"/>
</svg>''',

    # Text Generation WebUI - Terminal Prompt & Neural Text
    'templates/text-generation-webui.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#18181B"/>
  <rect x="12" y="16" width="40" height="32" rx="4" stroke="#10B981" stroke-width="2.5" fill="#064E3B" fill-opacity="0.2"/>
  <path d="M18 26 L24 32 L18 38" stroke="#10B981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <line x1="26" y1="38" x2="34" y2="38" stroke="#10B981" stroke-width="2.5" stroke-linecap="round"/>
</svg>''',

    # Whisper-X - Acoustic Speech Recognition Waveform
    'templates/whisper-x.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#0F172A"/>
  <circle cx="32" cy="32" r="14" stroke="#0EA5E9" stroke-width="3" fill="#0369A1" fill-opacity="0.2"/>
  <path d="M26 26 L38 38 M38 26 L26 38" stroke="#38BDF8" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 32 C12 21 21 12 32 12" stroke="#0284C7" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M52 32 C52 43 43 52 32 52" stroke="#0284C7" stroke-width="2.5" stroke-linecap="round"/>
</svg>''',

    # Bark TTS - Audio Synthesizer Dog Silhouette / Wave
    'templates/bark-tts.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#1C1917"/>
  <path d="M20 22 C20 18 24 16 28 18 L38 24 L46 22 L46 32 L40 34 L38 44 L28 44 L28 36 L24 36 L20 44 L16 44 L18 32 Z" fill="#D97706"/>
  <circle cx="28" cy="23" r="2" fill="#1C1917"/>
  <path d="M48 26 C51 28 51 32 48 34" stroke="#F59E0B" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M52 23 C56 26 56 34 52 37" stroke="#FCD34D" stroke-width="2.5" stroke-linecap="round"/>
</svg>''',

    # Coqui TTS - Phonetic Frog Mascot & Audio Wave
    'templates/coqui-tts.svg': '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect width="64" height="64" rx="14" fill="#064E3B"/>
  <circle cx="24" cy="24" r="6" fill="#10B981"/>
  <circle cx="40" cy="24" r="6" fill="#10B981"/>
  <circle cx="24" cy="23" r="2.5" fill="#064E3B"/>
  <circle cx="40" cy="23" r="2.5" fill="#064E3B"/>
  <path d="M18 32 C18 26 46 26 46 32 C46 42 18 42 18 32 Z" fill="#10B981"/>
  <path d="M24 34 Q32 40 40 34" stroke="#064E3B" stroke-width="2.5" stroke-linecap="round" fill="none"/>
</svg>''',
}

# Cross-copies between Addons and Templates
CROSS_COPIES = [
    # (source_rel, dest_rel)
    ('addons/cassandra.svg', 'templates/cassandra.svg'),
    ('templates/weaviate.svg', 'addons/weaviate.svg'),
    ('addons/milvus.svg', 'templates/milvus.svg'),
    ('templates/huggingface.svg', 'templates/tgi.svg'),
]

def download_file(rel_path, url):
    full_path = os.path.join(FRONTEND_PUBLIC, rel_path)
    try:
        r = requests.get(url, timeout=6)
        if r.status_code == 200 and len(r.content) > 100:
            with open(full_path, 'wb') as f:
                f.write(r.content)
            print(f"[OK] Downloaded: {rel_path} ({len(r.content)}b) from {url}")
            return True
        else:
            print(f"[FAIL] Failed {rel_path}: HTTP {r.status_code}")
            return False
    except Exception as e:
        print(f"[ERR] Error downloading {rel_path}: {e}")
        return False

def write_dedicated(rel_path, content):
    full_path = os.path.join(FRONTEND_PUBLIC, rel_path)
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(content.strip())
    print(f"[INSTALLED] Installed dedicated SVG: {rel_path} ({len(content)}b)")

def copy_asset(src_rel, dest_rel):
    src = os.path.join(FRONTEND_PUBLIC, src_rel)
    dst = os.path.join(FRONTEND_PUBLIC, dest_rel)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"[COPY] Copied {src_rel} -> {dest_rel}")
    else:
        print(f"[WARN] Source missing for copy: {src_rel}")

def sync_to_backend():
    print("\nSyncing all logos to backend static directories...")
    # Sync templates
    for f in os.listdir(TEMPLATES_DIR):
        src = os.path.join(TEMPLATES_DIR, f)
        dst = os.path.join(BACKEND_TEMPLATES_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
    # Sync addons
    for f in os.listdir(ADDONS_DIR):
        src = os.path.join(ADDONS_DIR, f)
        dst = os.path.join(BACKEND_ADDONS_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
    print(f"[SYNC] Synced {len(os.listdir(TEMPLATES_DIR))} templates to backend")
    print(f"[SYNC] Synced {len(os.listdir(ADDONS_DIR))} addons to backend")

def main():
    print("=== Step 1: Writing Dedicated Custom SVGs ===")
    for rel_path, svg in DEDICATED_SVGS.items():
        write_dedicated(rel_path, svg)

    print("\n=== Step 2: Downloading Verified Official Logos ===")
    for rel_path, url in URL_SOURCES.items():
        download_file(rel_path, url)

    print("\n=== Step 3: Cross-copying verified internal assets ===")
    for src_rel, dst_rel in CROSS_COPIES:
        copy_asset(src_rel, dst_rel)

    print("\n=== Step 4: Syncing to Backend Static ===")
    sync_to_backend()

    print("\n=== All logos updated successfully! ===")

if __name__ == '__main__':
    main()
