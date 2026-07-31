import re

base = r"C:\Users\osaretin\Documents\SMSLY\SMSLY_CORE\smsly-hosting\backend\apps\autoscaler\migrations\0003_servicemetric_servicereplica.py"
with open(base, "r", encoding="utf-8") as f:
    lines = f.readlines()

start = None
end = None
for i, line in enumerate(lines):
    if re.match(r"^\s{4}operations = \[\s*$", line):
        start = i
    if start is not None and i > start and re.match(r"^\s{4}\]\s*$", line):
        end = i
        break

assert start is not None and end is not None, f"start={start} end={end}"

new_block = [
    "    operations = [\n",
    "        migrations.SeparateDatabaseAndState(\n",
    "            state_operations=[\n",
]
for i in range(start + 1, end):
    new_block.append("    " + lines[i])
new_block.append("            ],\n")
new_block.append("            database_operations=[],\n")
new_block.append("        ),\n")
new_block.append("    ]\n")

out = lines[:start] + new_block + lines[end + 1:]
with open(base, "w", encoding="utf-8", newline="") as f:
    f.writelines(out)
print("autoscaler.0003 rewritten OK")
