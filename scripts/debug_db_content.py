import sys
import os
# Add backend to path
sys.path.append(os.getcwd())

from sqlalchemy import create_engine, text
from app.config import get_settings

def check_db():
    settings = get_settings()
    print(f"Connecting to: {settings.database_url.split('@')[-1]}") # Hide creds
    
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            print("\n--- CHECKING SCHEMAS ---")
            result = conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
            schemas = [r[0] for r in result]
            
            if 'analytics' in schemas:
                print("[YES] Schema 'analytics' FOUND")
            else:
                print("[NO] Schema 'analytics' MISSING")
                
            if 'identity' in schemas:
                print("[YES] Schema 'identity' FOUND")
            else:
                print("[NO] Schema 'identity' MISSING")

            print("\n--- CHECKING TABLES ---")
            # Query for tables in our specific schemas
            sql = text("""
                SELECT table_schema, table_name 
                FROM information_schema.tables 
                WHERE table_schema IN ('analytics', 'identity', 'public')
                ORDER BY table_schema, table_name
            """)
            tables = conn.execute(sql).fetchall()
            
            if not tables:
                print("No tables found in analytics, identity, or public schemas!")
            else:
                for t in tables:
                    print(f"[FOUND] Found table: {t[0]}.{t[1]}")

    except Exception as e:
        print(f"Error connecting: {e}")

if __name__ == "__main__":
    check_db()
