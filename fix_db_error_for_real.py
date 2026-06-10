with open('backend/config/settings.py', 'r') as f:
    content = f.read()

import re

# We need to catch `relation "deployments_platformconfig" does not exist`
# Currently it looks like:
# if 'relation "deployments_platformconfig" does not exist' not in str(e):
content = content.replace("        if 'relation \"deployments_platformconfig\" does not exist' not in str(e):", "        if 'relation \"deployments_platformconfig\" does not exist' not in str(e) and 'deployments_platformconfig' not in str(e):")

with open('backend/config/settings.py', 'w') as f:
    f.write(content)
