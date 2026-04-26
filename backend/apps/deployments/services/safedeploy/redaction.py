import re

def redact_secrets(text: str) -> str:
    if not text: return text
    patterns = [
        (r'(DATABASE_URL\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(SECRET_KEY\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(API_KEY\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(TOKEN\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(PASSWORD\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(://[^:]+:)([^@]+)(@)', r'\1[REDACTED]\3'),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted
