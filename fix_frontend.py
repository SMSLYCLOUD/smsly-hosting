with open('frontend/src/app/docs/install/page.tsx', 'r') as f:
    content = f.read()

content = content.replace(
    '<p>The <strong>Access URL</strong> is always <code>http://YOUR_SERVER_IP</code> for a fresh install — never HTTPS. See "Accessing the Dashboard" below for why.</p>',
    '<p>The <strong>Access URL</strong> is always <code>http://YOUR_SERVER_IP</code> for a fresh install &mdash; never HTTPS. See &quot;Accessing the Dashboard&quot; below for why.</p>'
)

with open('frontend/src/app/docs/install/page.tsx', 'w') as f:
    f.write(content)
