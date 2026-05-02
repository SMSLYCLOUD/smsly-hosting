import sqlite3
import os

db_path = 'c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend/db.sqlite3'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("SELECT name, host, is_primary, status FROM deployments_managedserver")
    rows = cursor.fetchall()
    print(f"{'Name':<20} | {'Host':<20} | {'Primary':<8} | {'Status'}")
    print("-" * 60)
    for row in rows:
        print(f"{row[0]:<20} | {row[1]:<20} | {str(row[2]):<8} | {row[3]}")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
