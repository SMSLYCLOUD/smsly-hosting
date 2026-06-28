path = "backend/entrypoint.sh"
with open(path, "r") as f:
    content = f.read()
old = "exec su -s /bin/sh -c 'exec \"$@\"' smsly -- \"$@\""
new = "exec su -s /bin/sh -c 'exec \"$0\" \"$@\"' smsly -- \"$@\""
content = content.replace(old, new)
with open(path, "w") as f:
    f.write(content)
print("Fixed entrypoint")
