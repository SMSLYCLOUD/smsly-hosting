import sqlite3
import os

db_path = 'c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend/db.sqlite3'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("SELECT name, host, id FROM deployments_managedserver WHERE is_primary = 1")
    row = cursor.fetchone()
    if row:
        print(f"Primary Server: {row[0]} ({row[1]}) ID: {row[2]}")
    else:
        print("No Primary Server found.")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
