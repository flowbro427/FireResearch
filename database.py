import sqlite3
import pandas as pd
from datetime import datetime
import json # Added json for tags

DB_NAME = "etsy_opportunities.db"

def initialize_db():
    """Initializes the SQLite database and creates the opportunities table if it doesn't exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Check if 'everbee_tags' column exists
    cursor.execute("PRAGMA table_info(opportunities)")
    columns = [info[1] for info in cursor.fetchall()]
    
    if 'everbee_tags' not in columns:
        try:
            # Add the new column if it doesn't exist (for existing databases)
            cursor.execute("ALTER TABLE opportunities ADD COLUMN everbee_tags TEXT")
            print("Added 'everbee_tags' column to opportunities table.")
        except sqlite3.OperationalError as e:
            # Handle case where table doesn't exist yet (will be created below)
            if "no such table" not in str(e):
                 print(f"Warning: Could not add column 'everbee_tags': {e}")
                 # Potentially raise the error if it's unexpected
                 # raise e

    # Create table if it doesn't exist (includes all columns)
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
            everbee_tags TEXT
        )
    ''')
    # Add shipping_cost column if it doesn't exist (for backward compatibility)
    try:
        cursor.execute('ALTER TABLE opportunities ADD COLUMN shipping_cost REAL')
        print("Added shipping_cost column to opportunities table.")
    except sqlite3.OperationalError as e:
        if 'duplicate column name' not in str(e):
            raise e # Reraise if it's not the expected error
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
        
        cursor.execute("""
            INSERT INTO opportunities (
                product_title, price, product_url, shop_name, shop_url, 
                niche_tags, est_monthly_revenue, est_monthly_sales, shop_age, 
                processing_time, shipping_cost, aliexpress_urls, is_digital, notes,
                total_sales, views, favorites, conversion_rate, listing_age, 
                shop_age_overall, category, visibility_score, review_ratio, 
                monthly_reviews, listing_type, everbee_tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            data.get('product_title'), data.get('price'), data.get('product_url'), 
            data.get('shop_name'), data.get('shop_url'), data.get('niche_tags'), 
            data.get('est_monthly_revenue'), data.get('est_monthly_sales'), data.get('shop_age'),
            data.get('processing_time'), data.get('shipping_cost'), data.get('aliexpress_urls'),
            data.get('is_digital', False), data.get('notes'),
            data.get('total_sales'), data.get('views'), data.get('favorites'), 
            data.get('conversion_rate'), data.get('listing_age'), data.get('shop_age_overall'), 
            data.get('category'), data.get('visibility_score'), data.get('review_ratio'),
            data.get('monthly_reviews'), data.get('listing_type'), tags_json
        ))
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