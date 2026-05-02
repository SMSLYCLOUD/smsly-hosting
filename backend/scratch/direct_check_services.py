import sqlite3
import os

db_path = 'c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend/db.sqlite3'
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("SELECT name, status, server_id, id FROM deployments_service")
    rows = cursor.fetchall()
    print(f"{'Name':<30} | {'Status':<15} | {'ServerID':<20} | {'ID'}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]:<30} | {row[1]:<15} | {str(row[2]):<20} | {row[3]}")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
