import sqlite3
import pandas as pd
from datetime import datetime, date
import json # Added json for tags
import numpy as np

DB_NAME = "etsy_opportunities.db"

def initialize_db():
    """Initializes the SQLite database and creates/updates tables."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # --- Schema Versioning/Migration (Opportunities Table) --- 
    cursor.execute("PRAGMA table_info(opportunities)")
    columns_opp = [info[1] for info in cursor.fetchall()]
    required_columns_opp = [
        ('everbee_tags', 'TEXT'),
        ('last_30_days_sales', 'INTEGER'),
        ('last_30_days_revenue', 'REAL')
    ]
    for col_name, col_type in required_columns_opp:
        if col_name not in columns_opp:
            try:
                cursor.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_type}")
                print(f"Added '{col_name}' column to opportunities table.")
            except sqlite3.OperationalError as e:
                if "no such table" not in str(e): 
                    print(f"Warning: Could not add column '{col_name}' to opportunities: {e}")

    # --- Create Opportunities Table (if it doesn't exist) --- 
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

    # --- Update ERANK Analyses Table (Add country_code column) ---
    cursor.execute("PRAGMA table_info(erank_keyword_analyses)")
    columns_erank = {info[1]: info[2] for info in cursor.fetchall()} # name: type
    
    # Check if country_code exists, if not, migrate
    if 'country_code' not in columns_erank:
         print("DEBUG DB: 'country_code' column missing from erank_keyword_analyses. Attempting migration...")
         try:
            # Create new table with the column
            cursor.execute('''
                CREATE TABLE erank_keyword_analyses_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    seed_keyword TEXT,
                    weights TEXT,
                    country_code TEXT -- Added column
                )
            ''')
            # Copy data (handle potential missing columns in old table if it was partially migrated)
            copy_cols_list = ['id', 'analyzed_at', 'seed_keyword', 'weights']
            copy_cols_sql = ', '.join(copy_cols_list)
            if all(col in columns_erank for col in copy_cols_list):
                 cursor.execute(f'''
                     INSERT INTO erank_keyword_analyses_new ({copy_cols_sql}) 
                     SELECT {copy_cols_sql} FROM erank_keyword_analyses
                 ''')
                 print("DEBUG DB: Copied data to new erank_keyword_analyses schema.")
            else:
                 print("Warning DB: Could not copy data to new erank_keyword_analyses schema due to missing source columns.")
                 
            # Drop old table
            cursor.execute('DROP TABLE erank_keyword_analyses')
            # Rename new table
            cursor.execute('ALTER TABLE erank_keyword_analyses_new RENAME TO erank_keyword_analyses')
            print("Successfully migrated erank_keyword_analyses table to include 'country_code'.")
            conn.commit()
         except sqlite3.Error as e:
             print(f"ERROR DB: Failed to migrate erank_keyword_analyses table for country_code: {e}.")
             conn.rollback()
             # Fallback: Create if not exists with the new column if migration failed
             cursor.execute('''
                 CREATE TABLE IF NOT EXISTS erank_keyword_analyses (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     seed_keyword TEXT,
                     weights TEXT,
                     country_code TEXT
                 )
             ''')
    else:
         # If country_code exists, still ensure table exists
         cursor.execute('''
             CREATE TABLE IF NOT EXISTS erank_keyword_analyses (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 seed_keyword TEXT,
                 weights TEXT,
                 country_code TEXT
             )
         ''')
            
    # --- ERANK Keywords Table --- 
    # More robust check/migration for added_at column
    cursor.execute("PRAGMA table_info(erank_keywords)")
    columns_erank_kw = {info[1]: info[2] for info in cursor.fetchall()} # Name: Type
    added_at_exists = 'added_at' in columns_erank_kw
    # old_date_col_exists = 'data_date_str' in columns_erank_kw # No longer needed
    
    # Schema definition WITHOUT default timestamp
    correct_schema_sql = '''
        CREATE TABLE erank_keywords_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER,
            keyword TEXT,
            added_at TIMESTAMP, -- Removed DEFAULT CURRENT_TIMESTAMP
            avg_searches_str TEXT,
            avg_clicks_str TEXT,
            avg_ctr_str TEXT,
            etsy_competition_str TEXT,
            google_searches_str TEXT,
            FOREIGN KEY (analysis_id) REFERENCES erank_keyword_analyses (id)
        )
    '''
    
    # Migration logic remains largely the same, but uses the new schema definition
    if not added_at_exists:
        print("DEBUG DB: 'added_at' column missing from erank_keywords. Attempting migration...")
        try:
            # Create the new table with the correct schema
            cursor.execute(correct_schema_sql)
            
            # If the old table exists, copy data (omitting old date column if present)
            if columns_erank_kw: # Check if old table existed
                 # Adjust columns to copy based on old schema possibility
                 copy_columns_list = ['id', 'analysis_id', 'keyword', 'avg_searches_str', 'avg_clicks_str', 'avg_ctr_str', 'etsy_competition_str', 'google_searches_str']
                 # Check if necessary source columns exist in the old table
                 can_copy = all(col in columns_erank_kw for col in copy_columns_list)
                 
                 if can_copy:
                     copy_columns_sql = ', '.join(copy_columns_list)
                     cursor.execute(f'''
                         INSERT INTO erank_keywords_new ({copy_columns_sql}) 
                         SELECT {copy_columns_sql} FROM erank_keywords
                     ''')
                     print("DEBUG DB: Copied data to new erank_keywords schema (without added_at).")
                 else:
                     print("Warning DB: Could not copy data to new erank_keywords schema due to missing source columns.")

            # Drop the old table
            cursor.execute('DROP TABLE erank_keywords')
            # Rename the new table
            cursor.execute('ALTER TABLE erank_keywords_new RENAME TO erank_keywords')
            print("Successfully migrated erank_keywords table to include 'added_at' (without default).")
            conn.commit() # Commit migration changes immediately
        except sqlite3.Error as e:
             print(f"ERROR DB: Failed to migrate erank_keywords table: {e}. Attempting simple CREATE IF NOT EXISTS.")
             conn.rollback() # Rollback failed migration
             # Fallback: just try to create it if migration failed
             # Use correct_schema_sql but create directly if not exists
             create_sql = correct_schema_sql.replace('_new', '').replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS')
             cursor.execute(create_sql)
    else:
         # If added_at already exists, ensure table exists anyway (idempotent)
         create_sql = correct_schema_sql.replace('_new', '').replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS')
         cursor.execute(create_sql)
            
    # --- Backfill NULL added_at dates (Revised) --- 
    try:
        update_timestamp = datetime.now() # Get timestamp once
        # Directly update rows where added_at is NULL
        cursor.execute("UPDATE erank_keywords SET added_at = ? WHERE added_at IS NULL", (update_timestamp,))
        # REMOVED print
        # if cursor.rowcount > 0:
        #     print(f"DEBUG DB: Backfilled {cursor.rowcount} erank_keywords rows with NULL added_at to {update_timestamp}.")
        #     conn.commit() # Commit the backfill immediately
        # else:
        #     # print("DEBUG DB: No NULL added_at values found to backfill.")
        #     pass # No rows needed updating
        # Commit happens later anyway
    except sqlite3.Error as e:
         print(f"Warning DB: Could not backfill NULL added_at values: {e}")

    # --- ADDED: Create saved_shops table --- 
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS saved_shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_url TEXT NOT NULL UNIQUE, 
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    # --- END ADDED --- 

    conn.commit() # Final commit for any table creations/migrations earlier
    conn.close()
    print("Database initialized successfully.")

def add_opportunity(data):
    """Adds a new opportunity to the database. Returns the ID of the inserted row or None if failed."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # Prepare data, converting list-based fields to JSON
        tags_json = None
        if 'everbee_tags' in data and isinstance(data['everbee_tags'], list):
            try:
                tags_json = json.dumps(data['everbee_tags'])
            except TypeError as e:
                print(f"Error converting Everbee tags to JSON: {e}.")
                tags_json = None

        # Prepare column names and placeholders dynamically
        valid_data = {k: v for k, v in data.items() if v is not None}
        
        # Overwrite with JSON strings if conversion was successful
        if tags_json is not None:
            valid_data['everbee_tags'] = tags_json
        else:
            valid_data.pop('everbee_tags', None)

        # Ensure boolean is 0 or 1 if present
        if 'is_digital' in valid_data:
             valid_data['is_digital'] = 1 if valid_data['is_digital'] else 0
        if 'is_potential_dropshipper' in valid_data:
             valid_data['is_potential_dropshipper'] = 1 if valid_data['is_potential_dropshipper'] else 0
            
        columns = ', '.join(valid_data.keys())
        placeholders = ', '.join('?' * len(valid_data))
        sql = f'INSERT INTO opportunities ({columns}) VALUES ({placeholders});'
        values = tuple(valid_data.values())
        
        cursor.execute(sql, values)
        conn.commit()
        last_id = cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"Database Error adding opportunity: {e}") # Likely UNIQUE constraint failure on product_url
        last_id = None
    except Exception as e:
         print(f"Unexpected Database Error adding opportunity: {e}")
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
             print(f"Database Structure Warning fetching opportunities: {e}. Returning empty data.")
             return pd.DataFrame() # Return empty if table/column missing
        else:
             print(f"Unexpected OperationalError fetching opportunities: {e}")
             raise # Reraise other operational errors
    except Exception as e:
        print(f"Error fetching opportunities: {e}")
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
        print(f"Database error updating dropshipper flag: {e}")
    finally:
        conn.close()

# --- Functions for ERANK Data ---

def add_erank_analysis(seed_keyword, country_code, weights, raw_keyword_list):
    """Adds ERANK analysis metadata (incl. country) and upserts individual raw keywords, 
    considering keyword, country, and date for uniqueness."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    analysis_id = None
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    today_date = date.today() # Get today's date once
    # Format timestamp consistently WITHOUT microseconds for storage
    current_timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S') 

    try:
        conn.execute('BEGIN TRANSACTION')

        # 1. Add analysis metadata (including country_code)
        weights_json = json.dumps(weights) if weights else None
        cursor.execute(
            "INSERT INTO erank_keyword_analyses (seed_keyword, country_code, weights) VALUES (?, ?, ?)",
            (seed_keyword, country_code, weights_json)
        )
        analysis_id = cursor.lastrowid
        if not analysis_id:
             raise Exception("Failed to get analysis_id after insert.")

        # 2. Process individual keywords (Upsert logic based on keyword + country + date)
        if isinstance(raw_keyword_list, list):
            for kw_dict in raw_keyword_list:
                keyword_text = kw_dict.get('Keyword')
                if not keyword_text:
                    skipped_count += 1
                    continue # Skip if keyword text is missing

                # Check for existing keyword FOR THIS COUNTRY
                # We join to get the country code associated with past keyword entries
                cursor.execute("""
                    SELECT k.id, k.added_at 
                    FROM erank_keywords k
                    JOIN erank_keyword_analyses a ON k.analysis_id = a.id
                    WHERE k.keyword = ? AND a.country_code = ?
                    ORDER BY k.added_at DESC 
                    LIMIT 1
                """, (keyword_text, country_code))
                existing_row = cursor.fetchone() 

                # Prepare data tuple for insert/update (excluding id and added_at initially)
                # Note: analysis_id here links to the *current* analysis being saved.
                data_tuple = (
                    analysis_id, 
                    kw_dict.get('Avg Searches'),
                    kw_dict.get('Avg Clicks'),
                    kw_dict.get('Avg CTR'),
                    kw_dict.get('Etsy Competition'),
                    kw_dict.get('Google Searches')
                )

                if existing_row is None:
                    # --- No record found for this keyword + country combination: Insert new --- 
                    cursor.execute("""
                        INSERT INTO erank_keywords ( 
                            analysis_id, keyword, avg_searches_str, avg_clicks_str, 
                            avg_ctr_str, etsy_competition_str, google_searches_str, added_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (analysis_id, keyword_text) + data_tuple[1:] + (current_timestamp_str,))
                    inserted_count += 1
                else:
                    # --- Existing keyword found FOR THIS COUNTRY - check date --- 
                    existing_id = existing_row['id']
                    existing_added_at_str = existing_row['added_at']
                    existing_date = None
                    if existing_added_at_str:
                        try:
                            # Attempt parsing common formats, including potential microseconds
                            existing_dt = datetime.fromisoformat(existing_added_at_str.split('.')[0]) # Try removing microseconds first
                            existing_date = existing_dt.date()
                        except (ValueError, TypeError):
                            print(f"Warning: Could not parse existing date '{existing_added_at_str}' for keyword '{keyword_text}'")
                            existing_date = None # Treat unparseable date as different
                    
                    if existing_date == today_date:
                        # --- Skip (already added today FOR THIS COUNTRY) --- 
                        skipped_count += 1
                    else:
                        # --- Update existing keyword (different date FOR THIS COUNTRY) --- 
                        cursor.execute("""
                            UPDATE erank_keywords 
                            SET analysis_id = ?, 
                                avg_searches_str = ?, 
                                avg_clicks_str = ?, 
                                avg_ctr_str = ?, 
                                etsy_competition_str = ?, 
                                google_searches_str = ?, 
                                added_at = ? 
                            WHERE id = ?
                        """, data_tuple + (current_timestamp_str, existing_id))
                        updated_count += 1
        
        conn.commit()
        print(f"ERANK Save Summary: Processed {len(raw_keyword_list)} keywords for analysis ID {analysis_id} (Country: {country_code}). Inserted: {inserted_count}, Updated: {updated_count}, Skipped: {skipped_count}")
        
    except Exception as e:
        print(f"Database error during ERANK upsert: {e}")
        conn.rollback()
        analysis_id = None 
    finally:
        conn.close()
    return analysis_id

def get_all_erank_analyses():
    """Retrieves all ERANK analysis metadata entries (including country)."""
    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(erank_keyword_analyses)")
        columns = [info[1] for info in cursor.fetchall()]
        if not columns: return pd.DataFrame()

        # Select all columns including country_code
        cursor.execute("SELECT * FROM erank_keyword_analyses ORDER BY analyzed_at DESC")
        rows = cursor.fetchall()
        df = pd.DataFrame(rows, columns=columns)
        if 'analyzed_at' in df.columns:
             df['analyzed_at'] = pd.to_datetime(df['analyzed_at']).dt.strftime('%Y-%m-%d %H:%M')
        return df
    except Exception as e:
        print(f"Error fetching ERANK analysis metadata: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_all_erank_keywords():
    """Retrieves all saved ERANK keywords joined with their analysis country."""
    conn = sqlite3.connect(DB_NAME)
    try:
        # Use PRAGMA to get target columns for safety
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(erank_keywords)")
        kw_cols = [info[1] for info in cursor.fetchall()]
        cursor.execute("PRAGMA table_info(erank_keyword_analyses)")
        an_cols = [info[1] for info in cursor.fetchall()]
        
        if not kw_cols or 'analysis_id' not in kw_cols or 'id' not in kw_cols:
             print("Warning: erank_keywords table missing required columns (id, analysis_id). Cannot fetch.")
             return pd.DataFrame()
        if 'country_code' not in an_cols or 'id' not in an_cols:
             print("Warning: erank_keyword_analyses table missing required columns (id, country_code). Cannot fetch country.")
             # Fallback: Fetch without country if analysis table is missing column
             # You would need to implement get_all_erank_keywords_no_country() if needed
             # For now, we'll raise or return empty if country column is missing after migration attempt
             return pd.DataFrame() 

        # Join tables to get country code
        sql = """
            SELECT 
                k.id AS keyword_id, 
                k.analysis_id, 
                a.country_code, 
                k.keyword, 
                k.added_at, 
                k.avg_searches_str, 
                k.avg_clicks_str, 
                k.avg_ctr_str, 
                k.etsy_competition_str, 
                k.google_searches_str 
            FROM erank_keywords k
            LEFT JOIN erank_keyword_analyses a ON k.analysis_id = a.id
            ORDER BY k.id ASC
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        # Define DataFrame column names in the order of the SELECT statement
        df_columns = [
            'keyword_id', 'analysis_id', 'Country', 'Keyword', 'Added At', 
            'Avg Searches', 'Avg Clicks', 'Avg CTR', 'Etsy Competition', 'Google Searches' 
        ]
        
        df = pd.DataFrame(rows, columns=df_columns)
        
        # Format Added At date
        if 'Added At' in df.columns:
            try: 
                # Format to YYYY-MM-DD HH:MM:S (no microseconds)
                df['Added At'] = pd.to_datetime(df['Added At'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S') 
            except Exception as fmt_e:
                print(f"Warning formatting Added At in get_all_erank_keywords: {fmt_e}")

        return df
    except Exception as e:
        print(f"Error fetching all ERANK keywords with country: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

# Fallback function placeholder (if needed for robustness)
# def get_all_erank_keywords_no_country():
#     # ... implementation to fetch without country join ...
#     pass 

# --- ADDED: Saved Shops Functions --- 
def add_saved_shop(shop_url):
    """Adds a new shop URL to the saved_shops table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO saved_shops (shop_url) VALUES (?)", 
            (shop_url,)
        )
        conn.commit()
        inserted_id = cursor.lastrowid
        print(f"DEBUG DB: Successfully added shop URL '{shop_url}' with ID: {inserted_id}")
        conn.close()
        return True
    except sqlite3.IntegrityError: # Catch duplicate URL error
        print(f"DEBUG DB: Shop URL '{shop_url}' already exists.")
        conn.close()
        return False
    except Exception as e:
        print(f"ERROR DB: Error adding saved shop URL '{shop_url}': {e}")
        conn.rollback() # Rollback in case of other errors
        conn.close()
        return False

def get_all_saved_shops():
    """Retrieves all saved shop URLs from the database."""
    try:
        conn = sqlite3.connect(DB_NAME)
        query = "SELECT id, shop_url, added_at FROM saved_shops ORDER BY added_at DESC"
        df = pd.read_sql_query(query, conn)
        conn.close()
        print(f"DEBUG DB: Fetched {len(df)} saved shops.")
        return df
    except Exception as e:
        print(f"ERROR DB: Error fetching saved shops: {e}")
        return pd.DataFrame() # Return empty DataFrame on error
# --- END ADDED --- 