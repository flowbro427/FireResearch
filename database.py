import sqlite3
import pandas as pd
from datetime import datetime
import json # Added json for tags

DB_NAME = "etsy_opportunities.db"

def initialize_db():
    """Initializes the SQLite database and creates/updates the opportunities table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # --- Schema Versioning/Migration --- 
    cursor.execute("PRAGMA table_info(opportunities)")
    columns = [info[1] for info in cursor.fetchall()]

    # List of columns to ensure exist: (column_name, sql_type)
    required_columns = [
        ('everbee_tags', 'TEXT'),
        ('last_30_days_sales', 'INTEGER'),
        ('last_30_days_revenue', 'REAL') # Added
    ]

    for col_name, col_type in required_columns:
        if col_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_type}")
                print(f"Added '{col_name}' column to opportunities table.")
            except sqlite3.OperationalError as e:
                # Handle case where table doesn't exist yet (created below)
                if "no such table" not in str(e): 
                    print(f"Warning: Could not add column '{col_name}': {e}")

    # --- Create Table (if it doesn't exist) --- 
    # Includes all current columns
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            product_title TEXT NOT NULL,
            price REAL,
            product_url TEXT UNIQUE NOT NULL,
            shop_name TEXT,
            shop_url TEXT,
            niche_tags TEXT,
            est_monthly_revenue REAL,
            est_monthly_sales INTEGER,
            shop_age TEXT,
            processing_time TEXT,
            shipping_cost REAL,
            aliexpress_urls TEXT,
            is_digital BOOLEAN,
            is_potential_dropshipper BOOLEAN DEFAULT FALSE,
            notes TEXT,
            total_sales INTEGER,
            views INTEGER,
            favorites INTEGER,
            conversion_rate TEXT,
            listing_age TEXT,
            shop_age_overall TEXT,
            category TEXT,
            visibility_score TEXT,
            review_ratio TEXT,
            monthly_reviews INTEGER,
            listing_type TEXT,
            everbee_tags TEXT,
            last_30_days_sales INTEGER,
            last_30_days_revenue REAL 
        )
    ''')
            
    conn.commit()
    conn.close()
    print("Database initialized successfully.")

def add_opportunity(data):
    """Adds a new opportunity to the database. Returns the ID of the inserted row or None if failed."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # Prepare data, converting tags list to JSON
        tags_json = None
        if 'everbee_tags' in data and isinstance(data['everbee_tags'], list):
            try:
                tags_json = json.dumps(data['everbee_tags'])
            except TypeError as e:
                print(f"Error converting tags to JSON: {e}. Tags will be stored as null.")
                tags_json = None # Ensure it's None if dumping fails
        
        # Prepare column names and placeholders dynamically (more robust)
        # Filter out keys not present in data or None
        valid_data = {k: v for k, v in data.items() if v is not None}
        
        # Special handling for tags JSON
        if tags_json is not None:
            valid_data['everbee_tags'] = tags_json
        else:
            valid_data.pop('everbee_tags', None) # Remove if None
        
        # Ensure boolean is 0 or 1 if present
        if 'is_digital' in valid_data:
             valid_data['is_digital'] = 1 if valid_data['is_digital'] else 0
        if 'is_potential_dropshipper' in valid_data:
             valid_data['is_potential_dropshipper'] = 1 if valid_data['is_potential_dropshipper'] else 0
            
        columns = ', '.join(valid_data.keys())
        placeholders = ', '.join('?' * len(valid_data))
        sql = f'INSERT INTO opportunities ({columns}) VALUES ({placeholders});'
        values = tuple(valid_data.values())

        # print(f"DEBUG DB SQL: {sql}") # DEBUG
        # print(f"DEBUG DB Values: {values}") # DEBUG
        
        cursor.execute(sql, values)
        conn.commit()
        last_id = cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"Database Error: {e}") # Likely UNIQUE constraint failure on product_url
        last_id = None
    except Exception as e:
         print(f"Unexpected Database Error: {e}")
         last_id = None
    finally:
        conn.close()
    return last_id

def get_all_opportunities():
    """Retrieves all opportunities from the database as a Pandas DataFrame."""
    conn = sqlite3.connect(DB_NAME)
    try:
        # Get column names first to build DataFrame correctly even if table is empty
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(opportunities)")
        columns = [info[1] for info in cursor.fetchall()]
        if not columns:
            print("Warning: Opportunities table has no columns or does not exist?")
            return pd.DataFrame()
        
        cursor.execute("SELECT * FROM opportunities ORDER BY added_at DESC")
        rows = cursor.fetchall()
        df = pd.DataFrame(rows, columns=columns)
        
        # Format date_added for better readability if needed
        if 'added_at' in df.columns:
             df['added_at'] = pd.to_datetime(df['added_at']).dt.strftime('%Y-%m-%d %H:%M')
        return df
    except sqlite3.OperationalError as e:
        if "no such column" in str(e) or "no such table" in str(e):
             print(f"Database Structure Warning: {e}. Returning empty data. Ensure DB is initialized correctly.")
             return pd.DataFrame() # Return empty if table/column missing
        else:
             print(f"Unexpected OperationalError fetching data: {e}")
             raise # Reraise other operational errors
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame() # Return empty DataFrame on other errors
    finally:
        conn.close()

def delete_opportunity_by_id(opportunity_id):
    """Deletes an opportunity from the database based on its ID."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM opportunities WHERE id = ?", (opportunity_id,))
        conn.commit()
        if cursor.rowcount > 0:
            print(f"Successfully deleted opportunity with ID: {opportunity_id}")
            return True
        else:
            print(f"No opportunity found with ID: {opportunity_id}")
            return False
    except sqlite3.Error as e:
        print(f"Database error deleting opportunity ID {opportunity_id}: {e}")
        conn.rollback() # Rollback changes on error
        return False
    finally:
        conn.close()

def update_potential_dropshipper_flag(opportunity_id, is_potential):
    """Updates the is_potential_dropshipper flag for a given opportunity."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE opportunities
            SET is_potential_dropshipper = ?
            WHERE id = ?
        """, (is_potential, opportunity_id))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error updating flag: {e}")
    finally:
        conn.close() 