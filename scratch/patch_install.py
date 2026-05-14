import sys
import os

target = "install.sh"
with open(target, 'rb') as f:
    content = f.read()

# Note: The file uses CRLF or LF? 
# Let's check what we have in content.
# The hex showed 20 20 ... 79 (no trailing newline in the hex output from powershell)
# But there should be a newline in the file.

# Target content (12 spaces + command)
old = b'            docker compose -f "$COMPOSE_FILE" up -d db pgcat redis socket-proxy'

# Replacement (we'll try to detect the line ending from the file)
if b'\r\n' in content:
    nl = b'\r\n'
else:
    nl = b'\n'

replacement = (
    b'            if [ "$MODE_AGENT_LITE" = "true" ]; then' + nl +
    b'                docker compose -f "$COMPOSE_FILE" up -d socket-proxy' + nl +
    b'            else' + nl +
    b'                docker compose -f "$COMPOSE_FILE" up -d db pgcat redis socket-proxy' + nl +
    b'            fi'
)

new_content = content.replace(old, replacement)

if new_content == content:
    print("No changes made. Pattern not found.")
    sys.exit(1)

with open(target, 'wb') as f:
    f.write(new_content)
print(f"Successfully replaced {content.count(old)} occurrences.")
