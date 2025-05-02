import streamlit as st
import pandas as pd
import database as db
import json
from urllib.parse import quote_plus, urlparse, urlunparse
from bs4 import BeautifulSoup
from datetime import datetime, date
import re
import webbrowser
import time
import subprocess
import platform
import numpy as np

# --- Initialization ---
db.initialize_db() # Initialize DB early

# --- Opportunity Tracker Helper Functions ---
def generate_etsy_url(keyword, min_price=25):
    if not keyword: return ""
    query = quote_plus(keyword)
    return f"https://www.etsy.com/uk/search?q={query}&ref=search_bar&explicit=1&custom_price=1&min={min_price}&is_best_seller=true"

def clean_etsy_url(url):
    if not url or '?' not in url: return url
    return url.split('?')[0]

def calculate_days_until_delivery(date_str):
    """Calculates days from today until the estimated delivery date/range."""
    if not date_str:
        return ""

    today = date.today()
    current_year = today.year
    time_str = ""

    # Regex to find patterns like \"30 Apr\" or \"06-08 May\" 
    # Groups: 1=start_day, 2=end_day (optional), 3=month
    match = re.search(r'(\d{1,2})(?:-(\d{1,2}))?\s+([A-Za-z]{3})', date_str)
    if not match:
        return date_str # Return original string if format not recognized

    start_day = match.group(1)
    end_day = match.group(2) # This will be None if it's not a range like "DD-DD Month"
    month = match.group(3)
    
    start_date_str = f"{start_day} {month}" # Reconstruct e.g., "06 May"

    def parse_date_with_year(d_str):
        try:
            # Try parsing with current year
            dt = datetime.strptime(f"{d_str} {current_year}", '%d %b %Y').date()
            # If date is in the past, assume next year
            if dt < today:
                dt = datetime.strptime(f"{d_str} {current_year + 1}", '%d %b %Y').date()
            return dt
        except ValueError:
            return None # Handle parsing errors

    start_date = parse_date_with_year(start_date_str)
    if not start_date:
        return date_str # Return original if start date fails

    delta_start = (start_date - today).days

    if end_day: # If end_day (group 2) was captured, it's a DD-DD Month range
        end_date_str = f"{end_day} {month}" # Reconstruct e.g., "08 May"
        end_date = parse_date_with_year(end_date_str)
        if end_date:
            # Ensure end date is not before start date (handles year rollover)
            if end_date < start_date:
                 # Assume end date is next year relative to start_date's year
                 end_date = datetime.strptime(f"{end_date_str} {start_date.year + 1}", '%d %b %Y').date()

            delta_end = (end_date - today).days
            if delta_start >= 0 and delta_end >= 0:
                time_str = f"{delta_start}-{delta_end} days"
            else: # If calculated range is invalid, return original
                 time_str = date_str
        else:
             time_str = date_str # Return original if end date fails
    else: # Only a single date (e.g., "30 Apr") was found
        if delta_start >= 0:
            time_str = f"{delta_start} days"
        else: # If calculated date is invalid, return original
            time_str = date_str

    return time_str

def parse_etsy_html_content(html_content):
    """Parses pasted HTML content from an Etsy product page to extract details, prioritizing JSON-LD."""
    soup = BeautifulSoup(html_content, 'lxml')
    data = {
        'product_url': None,
        'product_title': None,
        'price_str': None,
        'shop_name': None,
        'shop_url': None, # Shop URL often not in JSON-LD, needs fallback
        'description_notes': None,
        'review_dates_str': None,
        'processing_time': None, # Will store calculated days
        'shipping_cost_str': None # Added shipping_cost_str
    }

    # --- Attempt 1: Parse JSON-LD Structured Data --- 
    json_ld_script = soup.find('script', {'type': 'application/ld+json'})
    if json_ld_script:
        try:
            structured_data = json.loads(json_ld_script.string)
            # Ensure it looks like product data
            if structured_data.get('@type') == 'Product':
                data['product_url'] = clean_etsy_url(structured_data.get('url'))
                data['product_title'] = structured_data.get('name')
                
                # Extract price from offers (handle single or aggregate)
                offers = structured_data.get('offers')
                if offers and isinstance(offers, dict):
                    if offers.get('@type') == 'AggregateOffer':
                        data['price_str'] = str(offers.get('lowPrice')) if offers.get('lowPrice') is not None else None
                    else: # Assume single offer
                        data['price_str'] = str(offers.get('price')) if offers.get('price') is not None else None
                
                # Extract shop name from brand
                brand = structured_data.get('brand')
                if brand and isinstance(brand, dict):
                    data['shop_name'] = brand.get('name')
                
                # Extract description
                data['description_notes'] = structured_data.get('description')
                
                # Extract review dates
                reviews = structured_data.get('review', [])
                review_dates = []
                if isinstance(reviews, list):
                    for review in reviews:
                        if isinstance(review, dict) and review.get('datePublished'):
                             # Date is already YYYY-MM-DD
                            review_dates.append(review['datePublished'])
                data['review_dates_str'] = ", ".join(review_dates)

        except json.JSONDecodeError:
            print("Error decoding JSON-LD") # Log error if needed
        except Exception as e:
             print(f"Error processing JSON-LD: {e}")

    # --- Attempt 2: Fallback to HTML Parsing (if key data missing from JSON or JSON failed) ---
    # Fallback for Product URL if not found in JSON-LD
    if not data.get('product_url'):
        canonical_link_tag = soup.find('link', {'rel': 'canonical'})
        if canonical_link_tag and canonical_link_tag.get('href'):
            data['product_url'] = clean_etsy_url(canonical_link_tag.get('href'))

    # Fallback for Product Title if not found in JSON-LD
    if not data.get('product_title'):
        title_tag = soup.find('h1')
        if title_tag: data['product_title'] = title_tag.get_text(strip=True)

    # Fallback for Price if not found in JSON-LD
    if not data.get('price_str'):
        price_parent = soup.find(attrs={"data-buy-box-region": "price"})
        if price_parent:
            # Look for common price paragraph classes, including wt-text-title-larger
            price_tag = price_parent.find('p', class_=lambda x: x and ('wt-text-title-larger' in x or 'wt-text-title-03' in x or 'wt-text-heading-03' in x))
            if price_tag:
                # Remove currency symbols, commas, and the trailing '+' if present
                price_text = price_tag.get_text(strip=True).replace('£', '').replace('$', '').replace(',', '').replace('+','')
                try:
                    data['price_str'] = str(float(price_text.split()[0]))
                except (ValueError, IndexError):
                    pass # Ignore if parsing fails
    
    # Fallback for Shop Name and URL (URL often requires this)
    if not data.get('shop_name') or not data.get('shop_url'):
        shop_link_tag = soup.find('a', href=lambda x: x and '/shop/' in x and 'reviews' not in x)
        if shop_link_tag:
            # Only overwrite shop_name if not found via JSON
            if not data.get('shop_name'):
                 data['shop_name'] = shop_link_tag.get_text(strip=True)
            # Always try to get shop URL from here
            shop_href = shop_link_tag.get('href')
            if shop_href:
                data['shop_url'] = clean_etsy_url(shop_href)

    # Fallback/Supplement for Description if not found in JSON-LD
    if not data.get('description_notes'):
        description_tag = soup.find('div', {'data-id': 'description-text'})
        if description_tag:
            data['description_notes'] = '\n'.join([p.get_text(strip=True) for p in description_tag.find_all('p')])

    # Fallback/Supplement for Review Dates if not found/incomplete in JSON-LD
    # (Could add logic to merge JSON dates and HTML dates if needed, but keep simple for now)
    if not data.get('review_dates_str'):
        review_dates_html = []
        reviews_section = soup.find('div', {'id': lambda x: x and x.startswith('reviews')})
        if reviews_section:
            date_tags = reviews_section.find_all('p', class_=lambda x: x and ('wt-text-caption' in x or 'wt-text-body-01' in x) and 'wt-text-gray' in x)
            for tag in date_tags:
                 date_text = tag.get_text(strip=True)
                 if ',' in date_text and any(month in date_text for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']):
                     try:
                         parsed_date = datetime.strptime(date_text, '%d %b, %Y')
                         review_dates_html.append(parsed_date.strftime('%Y-%m-%d'))
                     except ValueError:
                         pass
        if review_dates_html: # Only overwrite if HTML parsing found dates
            data['review_dates_str'] = ", ".join(review_dates_html)

    # --- Shipping Info (Always from HTML) ---
    est_delivery_days = ""
    shipping_cost_str = "" # Initialize here
    shipping_section = soup.find('div', {'id': 'shipping-and-returns-div'})
    
    if shipping_section:
        # --- Try original method first ---
        edd_li = shipping_section.find('li', {'data-shipping-estimated-delivery': True})
        if edd_li:
            edd_value_span = edd_li.find('span', {'data-shipping-edd-value': True})
            if edd_value_span:
                date_range_str = edd_value_span.get_text(strip=True)
                print(f"DEBUG Shipping: Attempt 1 - Found date string via span: '{date_range_str}'") # DEBUG
                est_delivery_days = calculate_days_until_delivery(date_range_str)
            else:
                # Fallback text search if specific span not found within edd_li
                 arrival_tag = edd_li.find(string=lambda t: t and ('get it by' in t.lower() or 'arrives by' in t.lower()))
                 if arrival_tag:
                     date_part = arrival_tag.strip().split('by')[-1].strip()
                     print(f"DEBUG Shipping: Attempt 2 - Found date string via text fallback within li: '{date_part}'") # DEBUG
                     est_delivery_days = calculate_days_until_delivery(date_part)
        
        # --- If original method failed, broaden the search within the section ---
        if not est_delivery_days:
            # Search for any common text element containing "Get it by" or "Arrives by"
            possible_tags = shipping_section.find_all(['p', 'span', 'div', 'li'])
            for tag in possible_tags:
                tag_text = tag.get_text(strip=True, separator=' ')
                if 'get it by' in tag_text.lower() or 'arrives by' in tag_text.lower():
                    try:
                        # Extract the part after " by " (case-insensitive)
                        date_part = re.split(r' by ', tag_text, flags=re.IGNORECASE)[-1].strip()
                        # Basic cleanup (remove leading/trailing non-alphanumeric, keeping spaces/hyphens needed for date)
                        date_part = re.sub(r'^[^\w\d]+|[^\w\d]+$', '', date_part).strip()
                        if date_part:
                            print(f"DEBUG Shipping: Attempt 3 - Found date string via broader fallback: '{date_part}'") # DEBUG
                            est_delivery_days = calculate_days_until_delivery(date_part)
                            if est_delivery_days and est_delivery_days != date_part: # Check if calculation succeeded
                                # print(f"DEBUG Shipping: Found date '{date_part}' via broader fallback.") # DEBUG - Redundant
                                break # Found a valid date, stop searching
                            else:
                                est_delivery_days = "" # Reset if calculation failed
                    except (IndexError, AttributeError) as e:
                        # print(f"DEBUG Shipping: Error processing tag text '{tag_text}': {e}") # DEBUG optional
                        pass # Ignore errors in this tag and continue searching
            # if not est_delivery_days:
                 # print("DEBUG Shipping: Delivery date pattern not found with any method.") # DEBUG - Redundant

        # --- Shipping Cost --- 
        # Find any element containing \"Delivery cost:\" (case-insensitive)
        cost_element = shipping_section.find(string=re.compile(r'delivery cost:', re.IGNORECASE))
        if cost_element:
            # Try to find the parent element that likely contains the price nearby
            # Navigating up one or two levels might be necessary depending on structure
            target_text_container = cost_element.parent 
            if target_text_container:
                 full_text = target_text_container.get_text(strip=True)
                 cost_match = re.search(r'[\£\$](\d+\.?\d*)', full_text)
                 if cost_match:
                     try:
                         shipping_cost_str = str(float(cost_match.group(1)))
                     except ValueError:
                         pass # Ignore if conversion fails
            # Fallback if parent didn't work, try cost_element itself
            if not shipping_cost_str:
                full_text = cost_element.strip() # Use the element text directly
                cost_match = re.search(r'[\£\$](\d+\.?\d*)', full_text)
                if cost_match:
                     try:
                         shipping_cost_str = str(float(cost_match.group(1)))
                     except ValueError:
                         pass
                         
        # --- Check for Free Delivery if no cost found --- 
        if not shipping_cost_str:
            free_delivery_element = shipping_section.find(string=re.compile(r'free delivery|free shipping', re.IGNORECASE))
            if free_delivery_element:
                shipping_cost_str = "0.0" # Set cost to 0 if free delivery text found
                print("DEBUG Shipping: Found 'Free Delivery' text.") # DEBUG
            else:
                print("DEBUG Shipping: Could not find numerical cost OR 'Free Delivery' text.") # DEBUG

    data['processing_time'] = est_delivery_days # Store calculated days
    data['shipping_cost_str'] = shipping_cost_str # Store found cost

    # Clean up None values to empty strings before returning
    for key in data:
        if data[key] is None:
            data[key] = ""

    return data

def parse_everbee_text_content(page_text):
    # --- Initial Setup ---
    parsed_data = {}
    notes = []
    all_rows_data = [] # To store data parsed from each potential product row

    lines = [line.strip() for line in re.split(r'\r\n?|\n', page_text) if line.strip()]
    if not lines:
        st.error("Error parsing Everbee text: No content found after splitting lines.")
        return None
    num_lines = len(lines)

    # --- Define Helper Functions for Type Conversion FIRST ---
    def safe_int(val_str): 
        return int(val_str.replace(',', '')) if val_str else None
    
    def safe_float_str(val_str):
        if not val_str: return None
        cleaned = re.sub(r'[^\d\.]', '', val_str) # Remove currency symbols, commas etc.
        try: return float(cleaned) if cleaned else None
        except ValueError: return None

    # --- Identify Potential Product Row Blocks --- 
    # Heuristic: Look for lines that seem like shop names (often contain uppercase/lowercase mix)
    # followed shortly by a price, sales, revenue pattern.
    row_start_indices = []
    for i, line in enumerate(lines):
        # Simple heuristic for a shop name (alphanumeric, maybe spaces, not just numbers/symbols)
        # And check if the next few lines contain likely price/sales/revenue
        is_potential_shop_name = re.match(r'^[A-Za-z0-9][A-Za-z0-9\s\-\'&]*[A-Za-z0-9]$', line) and not line.isdigit()
        if is_potential_shop_name and i > 0 and i + 3 < num_lines: 
            # Look ahead for Price ($), Mo. Sales (num), Mo. Revenue ($)
            potential_price = re.match(r'^[\$\£€]', lines[i+1])
            potential_sales = lines[i+2].isdigit()
            potential_revenue = re.match(r'^[\$\£€]', lines[i+3])
            # Check if the line *before* the potential shop name looks like a title (longish)
            potential_title_line = lines[i-1]
            is_likely_title = len(potential_title_line) > 20 # Arbitrary length check

            if is_likely_title and potential_price and potential_sales and potential_revenue:
                 # Assume the line *before* the shop name is the start of the row block's relevant data
                 row_start_indices.append(i - 1) 

    if not row_start_indices:
         st.warning("Could not reliably identify distinct product rows. Attempting single-row parse.")
         # Fallback to trying to parse as a single row (might work for the original example)
         row_start_indices.append(0) # Start from beginning

    # --- Parse Each Identified Row Block --- 
    highest_revenue = -1.0
    best_row_data = {}

    for idx, start_index in enumerate(row_start_indices):
        end_index = row_start_indices[idx + 1] if idx + 1 < len(row_start_indices) else num_lines
        row_lines = lines[start_index:end_index]
        
        if not row_lines: continue # Skip empty blocks

        current_row_raw = {}
        num_row_lines = len(row_lines)

        # --- Simplified Parsing within the Row Block --- 
        # Focus on Title, Shop, Price, Mo. Sales, Mo. Revenue first for selection
        current_row_raw['product_title'] = row_lines[0] # Assume first line is title
        current_row_raw['shop_name'] = row_lines[1] # Assume second line is shop name
        
        temp_price = None
        temp_sales = None
        temp_revenue = None

        # Look for the core pattern: Price, Sales, Revenue in lines 2, 3, 4 (relative to block start)
        if num_row_lines > 4:
             price_match = re.match(r'^([\$\£€]\d[\d,\.]*)$|', row_lines[2])
             sales_match = re.match(r'^(\d+)$|', row_lines[3])
             revenue_match = re.match(r'^([\$\£€]\d[\d,\.]*)$|', row_lines[4])
             if price_match and price_match.group(1):
                  current_row_raw['price_str'] = price_match.group(1)
                  temp_price = safe_float_str(current_row_raw['price_str'])
             if sales_match and sales_match.group(1):
                  current_row_raw['mo_sales'] = sales_match.group(1)
                  temp_sales = safe_int(current_row_raw['mo_sales'])
             if revenue_match and revenue_match.group(1):
                  current_row_raw['mo_revenue'] = revenue_match.group(1)
                  temp_revenue = safe_float_str(current_row_raw['mo_revenue'])

        # Store essential data for this row
        row_parsed_essential = {
             'product_title': current_row_raw.get('product_title'),
             'shop_name': current_row_raw.get('shop_name'),
             'price': temp_price,
             'monthly_sales': temp_sales,
             'monthly_revenue': temp_revenue,
             'price_str_display': current_row_raw.get('price_str'),
             'monthly_revenue_str_display': current_row_raw.get('mo_revenue')
        }
        all_rows_data.append(row_parsed_essential) # Keep track even if revenue is None
        
        # --- Compare Revenue for Best Row Selection --- 
        if temp_revenue is not None and temp_revenue > highest_revenue:
             highest_revenue = temp_revenue
             best_row_data = row_parsed_essential # Store the essential data of the best row
             # We will parse the rest of the details later, outside this loop

    if not best_row_data:
         st.error("Could not parse any row with valid Monthly Revenue to select the best one.")
         # Attempt to return data from the first parsed row if available
         if all_rows_data: 
             best_row_data = all_rows_data[0]
             st.warning("Falling back to the first identified row data as best could not be determined by revenue.")
         else:
             return None # Complete failure

    # --- Now Parse Remaining Details from the *ENTIRE* text, associating with best row --- 
    # Re-initialize parsed_data with the essential best row data
    parsed_data = best_row_data.copy()
    
    # --- IMPORTANT: Remove price info derived from Everbee --- 
    if 'price' in parsed_data:
        del parsed_data['price']
    if 'price_str_display' in parsed_data:
        del parsed_data['price_str_display']
        
    raw_values_full = {} # For parsing the rest of the fields from the full text

    # --- Define Keyword -> Value Pattern Mappings (Full Set) ---
    keyword_patterns_full = {
        # Pattern for VALUE is used for Shop Age
        'Mo. Sales': r'^(\d+)$|', 
        'Mo. Revenue': r'^([\$\£€]\d[\d,\.]*)$', 
        'Total sales': r'^(\d+)$|',
        'Listing age': r'^(\d+\s+months?|\d+\s+Mo\.)$|', # Pattern allows months OR Mo.
        #'Shop Age': r'^(\d+\s+Mo\.)$|', # REMOVED - Relying on counter
        'Reviews': r'^(\d+)$|',
        'Views': r'^(\d[\d,]*)$',
        'Favorites': r'^(\d[\d,]*)$',
        'Mo. Reviews': r'^(\d+)$|',
        'Conversion rate': r'^([\d.]+%?)$',
        'Category': r'^([A-Za-z][A-Za-z &/\s]+[A-Za-z])$|',
        'Visibility score': r'^(\d+%?)$|',
        'Review ratio': r'^([\d.]+%?)$|',
    }

    # --- Iterate through *ALL* lines looking for Keywords & Values for the full dataset ---
    mo_pattern_count = 0
    temp_listing_age_mo = None
    temp_shop_age_mo = None
    shop_age_pattern_val_only = r'^(\d+\s+Mo\.)$' # Just the value pattern
    
    for i, line in enumerate(lines):
        line_content_stripped = line.strip()
        if not line_content_stripped: continue # Skip blank lines

        found_as_label = False
        
        # --- Step 1: Check if line IS a known LABEL --- 
        for label, value_pattern in keyword_patterns_full.items():
            if label not in raw_values_full and line_content_stripped.lower() == label.lower():
                found_as_label = True
                matched_label = label
                # ... (Value finding logic for label on next line(s) - REMAINS SAME) ...
                value_match_obj = None
                current_value_pattern = value_pattern
                # Removed the incorrect if statement and duplicate print that caused the error
                
                # --- Find VALUE for this label on next line(s) --- 
                print(f"DEBUG LABEL FOUND: Line {i} is label '{matched_label}'") # DEBUG
                value_match_obj = None
                current_value_pattern = value_pattern # Use the specific pattern for this label
                # Try line i+1
                if i + 1 < num_lines:
                    match_next = re.match(current_value_pattern, lines[i+1].strip(), re.IGNORECASE)
                    if match_next and match_next.groups(): 
                        value_match_obj = match_next
                
                # Try line i+2 if i+1 was blank or didn't match
                if value_match_obj is None and i + 2 < num_lines and not lines[i+1].strip(): 
                    match_skip = re.match(current_value_pattern, lines[i+2].strip(), re.IGNORECASE)
                    if match_skip and match_skip.groups(): 
                        value_match_obj = match_skip
                
                # Store value if found
                if value_match_obj:
                    raw_values_full[matched_label] = value_match_obj.group(1).strip()
                    print(f"DEBUG VALUE STORED (for Label '{matched_label}'): '{raw_values_full[matched_label]}'") # DEBUG
                else:
                     print(f"DEBUG VALUE NOT FOUND for label '{matched_label}'") #DEBUG
                     
                break # Stop checking other labels for this line

        # --- Step 2: If line was NOT a label, check if it IS an 'XX Mo.' pattern --- 
        if not found_as_label:
            mo_match = re.match(shop_age_pattern_val_only, line_content_stripped, re.IGNORECASE)
            if mo_match and mo_match.group(1):
                mo_pattern_count += 1
                current_mo_value = mo_match.group(1).strip()
                print(f"DEBUG MO. MATCH: Found '{current_mo_value}', Count={mo_pattern_count}") # DEBUG
                if mo_pattern_count == 1:
                    temp_listing_age_mo = current_mo_value
                    print(f"DEBUG MO. ASSIGNED: Set Listing Age (Mo.) = {temp_listing_age_mo}") # DEBUG
                elif mo_pattern_count == 2:
                    temp_shop_age_mo = current_mo_value
                    print(f"DEBUG MO. ASSIGNED: Set Shop Age (Mo.) = {temp_shop_age_mo}") # DEBUG
                    # Can potentially break early if we only expect 2, but safer to continue

    # --- Add fully parsed data to the best_row data, converting types --- 
    # ... (Category assignment remains same) ...
    parsed_data['category'] = raw_values_full.get('Category')
    
    # Assign Listing Age: Prioritize direct Mo. match, fallback to label match
    if temp_listing_age_mo:
        parsed_data['listing_age'] = temp_listing_age_mo
    elif 'Listing age' in raw_values_full: # If label matched e.g. "X months"
         parsed_data['listing_age'] = raw_values_full['Listing age']
    elif 'listing_age' not in parsed_data: # If not found either way
         parsed_data['listing_age'] = None
         
    # Assign Shop Age: Use direct Mo. match if found
    if temp_shop_age_mo:
        parsed_data['shop_age_overall'] = temp_shop_age_mo
    elif 'Shop Age' in raw_values_full: # Fallback in case old logic stored something
        parsed_data['shop_age_overall'] = raw_values_full['Shop Age']
    elif 'shop_age_overall' not in parsed_data:
         parsed_data['shop_age_overall'] = None
         
    if 'listing_type' not in parsed_data: parsed_data['listing_type'] = raw_values_full.get('listing_type')
    
    # ... (rest of type conversions/assignments) ...
    # Numeric Conversions for full data
    def safe_int_full(key): 
        val_str = raw_values_full.get(key)
        return int(val_str.replace(',', '')) if val_str else None
    
    parsed_data['total_sales'] = safe_int_full('Total sales')
    parsed_data['reviews_count'] = safe_int_full('Reviews')
    parsed_data['views'] = safe_int_full('Views')
    parsed_data['favorites'] = safe_int_full('Favorites')
    parsed_data['monthly_reviews'] = safe_int_full('Mo. Reviews')
    # This was missing - ensure it's parsed if present in raw_values_full
    parsed_data['total_shop_sales'] = safe_int_full('total_shop_sales') 

    # Percentage/String fields
    parsed_data['conversion_rate'] = raw_values_full.get('Conversion rate')
    parsed_data['visibility_score'] = raw_values_full.get('Visibility score')
    parsed_data['review_ratio'] = raw_values_full.get('Review ratio')

    # --- Try to find Last 30 Days Sales (Trends data) ---
    # Heuristic: Look for "Sales" followed immediately by a number line
    # Search *after* potential product rows and core details, before Tags/Details
    # Define a reasonable search range (heuristic, might need adjustment)
    trends_search_start_index = len(row_start_indices) * 5 # Estimate past product rows
    trends_search_end_index = num_lines - 20 # Estimate before Tags/Details start
    trends_search_end_index = max(trends_search_start_index, trends_search_end_index) # Ensure end >= start
    
    last_30_sales_value = None
    for i in range(trends_search_start_index, trends_search_end_index):
        if lines[i].strip().lower() == 'sales' and i + 1 < num_lines:
            sales_val_match = re.match(r'^(\d+)$', lines[i+1].strip())
            if sales_val_match:
                # Potential check: Is there a Revenue line nearby?
                revenue_nearby = False
                for j in range(max(0, i - 1), min(num_lines, i + 4)):
                    if lines[j].strip().lower() == 'revenue':
                        revenue_nearby = True
                        break
                if revenue_nearby:
                    last_30_sales_value = sales_val_match.group(1)
                    print(f"DEBUG TRENDS: Found potential Last 30 Days Sales: {last_30_sales_value} at line index {i+1}") # DEBUG
                    break # Found it

    if last_30_sales_value:
        parsed_data['last_30_days_sales'] = last_30_sales_value
    else:
        print("DEBUG TRENDS: Did not find specific 'Last 30 Days Sales' pattern.") # DEBUG

    # --- Parse Tags Section (Simpler Sequential Approach) --- 
    tags_list = [] 
    # notes_tags_section = [] # REMOVED - Don't add tags to notes anymore
    try:
        # Find start and end markers for the tags block
        block_start_index = -1
        block_end_index = num_lines
        for i, line in enumerate(lines):
             # Use Keyword Score as primary start, fallback to Tags
             if re.match(r'^Keyword Score$', line, re.IGNORECASE):
                 block_start_index = i + 1; break
        if block_start_index == -1:
             for i, line in enumerate(lines):
                 if re.match(r'^Tags$', line, re.IGNORECASE):
                     block_start_index = i + 1
                     # Skip potential Volume/Competition headers
                     while block_start_index < num_lines and re.match(r'^(Volume|Competition)\s*$', lines[block_start_index], re.IGNORECASE):
                          block_start_index += 1
                     break
        for i in range(block_start_index if block_start_index != -1 else 0, num_lines):
            if re.match(r'^\s*More Details\s*$', lines[i], re.IGNORECASE):
                 block_end_index = i; break

        if block_start_index != -1 and block_start_index < block_end_index:
            tag_block_lines = lines[block_start_index:block_end_index]
            num_tag_lines = len(tag_block_lines)
            i = 0
            while i < num_tag_lines:
                # print(f"\\nDEBUG TAGS: Processing line index {i} within tag block. Content: '{tag_block_lines[i]}'") # DEBUG REMOVED
                # --- Check for 4/5 Line Pattern --- 
                current_tag = {}
                lines_consumed = 0

                # 1. Tag Name
                if i < num_tag_lines:
                    line1 = tag_block_lines[i].strip()
                    # Adjusted regex to be less strict, just look for some letters
                    if line1 and re.search(r'[a-zA-Z]', line1) and not re.match(r'^(?:[\\d,\\.]+|High|Medium|Low)$', line1, re.IGNORECASE):
                        current_tag['name'] = line1
                        lines_consumed += 1
                        # print(f"DEBUG TAGS: -> Matched Name: '{line1}'") # DEBUG REMOVED
                    else:
                        # print(f"DEBUG TAGS: -> Did NOT match Name pattern. Advancing i by 1.") # DEBUG REMOVED
                        i += 1; continue # Not a valid name start
                else: break # End of block

                # 2. Volume
                vol_idx = i + lines_consumed
                if vol_idx < num_tag_lines:
                    line2 = tag_block_lines[vol_idx].strip()
                    vol_match = re.match(r'^([\d,]+)$', line2) # Removed '|' from original regex
                    # SIMPLIFIED CONDITION:
                    if vol_match: # Removed 'and vol_match.group(1)'
                        current_tag['volume'] = vol_match.group(1)
                        lines_consumed += 1
                        # print(f"DEBUG TAGS: -> Matched Volume: '{line2}'") # DEBUG REMOVED
                    else:
                        # print(f"DEBUG TAGS: -> Did NOT match Volume pattern for line '{line2}'. Resetting and advancing i by 1.") # DEBUG REMOVED
                        i += 1; continue # Pattern broken
                else:
                    # print(f"DEBUG TAGS: -> End of block reached while looking for Volume. Breaking.") # DEBUG REMOVED
                    break # End of block
                
                # 3. Competition
                comp_idx = i + lines_consumed
                if comp_idx < num_tag_lines:
                    line3 = tag_block_lines[comp_idx].strip()
                    comp_match = re.match(r'^([\d,]+)$', line3) # Removed '|' from original regex
                    # SIMPLIFIED CONDITION:
                    if comp_match: # Removed 'and comp_match.group(1)'
                        current_tag['competition'] = comp_match.group(1)
                        lines_consumed += 1
                        # print(f"DEBUG TAGS: -> Matched Competition: '{line3}'") # DEBUG REMOVED
                    else:
                        # print(f"DEBUG TAGS: -> Did NOT match Competition pattern for line '{line3}'. Resetting and advancing i by 1.") # DEBUG REMOVED
                        i += 1; continue # Pattern broken
                else:
                    # print(f"DEBUG TAGS: -> End of block reached while looking for Competition. Breaking.") # DEBUG REMOVED
                    break # End of block

                # 4. Level (Optional) OR Score
                level_found = False
                level_idx = i + lines_consumed
                if level_idx < num_tag_lines:
                    line4 = tag_block_lines[level_idx].strip()
                    level_match = re.match(r'^(High|Medium|Low)$', line4, re.IGNORECASE) # Removed '|'
                    if level_match: # Already correct, no group(1) check here
                        current_tag['level'] = level_match.group(1)
                        lines_consumed += 1
                        level_found = True
                        # print(f"DEBUG TAGS: -> Matched Level: '{line4}'") # DEBUG REMOVED
                    else:
                        current_tag['level'] = 'N/A' # Default if line 4 isn't level
                        # print(f"DEBUG TAGS: -> Did NOT match Level pattern for line '{line4}'. Assuming N/A.") # DEBUG REMOVED
                else:
                    # print(f"DEBUG TAGS: -> End of block reached while looking for Level. Breaking.") # DEBUG REMOVED
                    break # End of block
                
                # 5. Score (Required - check at index + lines_consumed)
                score_line_index = i + lines_consumed
                if score_line_index < num_tag_lines:
                    line5 = tag_block_lines[score_line_index].strip()
                    # FIX: Removed extra single quote from regex end
                    score_match = re.match(r'^([\d,\.]+)$', line5)
                    # SIMPLIFIED CONDITION:
                    if score_match: # Removed 'and score_match.group(1)'
                        current_tag['score'] = score_match.group(1)
                        lines_consumed += 1 # Consume score line
                        # print(f"DEBUG TAGS: -> Matched Score: '{line5}'") # DEBUG REMOVED
                        # Successfully parsed a tag!
                        tags_list.append(current_tag)
                        # print(f"DEBUG TAGS: *** Successfully added tag: {current_tag}. Advancing i by {lines_consumed}. ***") # DEBUG REMOVED
                        i += lines_consumed # Advance main index
                        continue # Go to next potential tag start
                    else: # Score not found where expected
                         # print(f"DEBUG TAGS: -> Did NOT match Score pattern for line '{line5}'. Resetting and advancing i by 1.") # DEBUG REMOVED
                         i += 1; continue # Pattern broken
                else:
                    # print(f"DEBUG TAGS: -> End of block reached while looking for Score. Breaking.") # DEBUG REMOVED
                    break # End of block
                
                # If we somehow fall through without continuing/breaking (shouldn't happen)
                # print(f"DEBUG TAGS: -> Unexpected fallthrough. Advancing i by 1.") # DEBUG REMOVED
                i+= 1

            # --- Assign to parsed_data --- 
            if tags_list:
                parsed_data['tags_list'] = tags_list # Ensure the list of dicts is assigned
                # REMOVED tag formatting for notes:
                # notes_tags_section.append("\n--- Everbee Tags ---")
                # for tag_dict in tags_list:
                #     level_str = tag_dict.get('level', 'N/A')
                #     notes_tags_section.append(f"- Tag: {tag_dict.get('name', '?')}, Vol: {tag_dict.get('volume', '?')}, Comp: {tag_dict.get('competition', '?')}, Level: {level_str}, Score: {tag_dict.get('score', '?')}")
        else:
             pass # Just means no tags block found

    except Exception as e:
        # print(f"DEBUG TAGS: EXCEPTION during parsing: {e}") # DEBUG REMOVED
        notes.append(f"\n--- Everbee Tags ---\nError parsing tags: {e}") # Keep error reporting in notes

    # --- Parse More Details Section (using full lines list) --- 
    try:
        details_start_index = -1
        for i, line in enumerate(lines):
             if re.match(r'^\s*More Details\s*$', line, re.IGNORECASE):
                 details_start_index = i + 1
                 break
        
        if details_start_index != -1:
            details_list = []
            known_keys = ["When Made", "Listing Type", "Customizable", "Craft Supply", "Personalized", "Auto Renew", "Has variations", "Placements of Listing Shops", "Title character count", "# of tags", "Who Made"]
            key_regex_map = {key: re.compile(r'^\s*' + re.escape(key) + r'\s*$', re.IGNORECASE) for key in known_keys}
            
            current_key = None
            current_value_lines = []

            for i in range(details_start_index, num_lines):
                line = lines[i]
                is_known_key = False
                matched_key = None
                for key, key_regex in key_regex_map.items():
                    if key_regex.match(line):
                         is_known_key = True; matched_key = key; break

                if is_known_key:
                    if current_key and current_value_lines:
                        value = ' '.join(current_value_lines).strip()
                        # Handle simple values possibly on the key's line (look back)
                        if not value and i > details_start_index:
                             prev_line = lines[i-1]
                             same_line_match = re.match(r'^\s*' + re.escape(current_key) + r'\s+(.+)$', prev_line, re.IGNORECASE)
                             if same_line_match:
                                 value = same_line_match.group(1).strip()
                        # Clean up specific values
                        if current_key == 'Who Made' and isinstance(value, str):
                            value = re.sub(r'\s+\d+$', '', value).strip()
                        details_list.append({'key': current_key, 'value': value or 'Unknown'})
                        # Ensure listing type is updated in main dict
                        if current_key == 'Listing Type': 
                            parsed_data['listing_type'] = value or 'Unknown' # Overwrite previous guess
                    
                    current_key = matched_key
                    current_value_lines = []
                    # Check for value on same line as key
                    same_line_match = re.match(r'^\s*' + re.escape(matched_key) + r'\s+(.+)$', line, re.IGNORECASE)
                    if same_line_match:
                        current_value_lines.append(same_line_match.group(1).strip())
                elif current_key:
                     current_value_lines.append(line)

            if current_key and current_value_lines:
                value = ' '.join(current_value_lines).strip()
                details_list.append({'key': current_key, 'value': value or 'Unknown'})
                # Ensure listing type is updated in main dict
                if current_key == 'Listing Type':
                    parsed_data['listing_type'] = value or 'Unknown' # Overwrite previous guess

            if details_list:
                 parsed_data['more_details_list'] = details_list
                 notes.append("\n--- Everbee More Details ---")
                 for detail_dict in details_list:
                     notes.append(f"- {detail_dict['key']}: {detail_dict['value']}")
    except Exception as e:
        notes.append(f"\n--- Everbee More Details ---\nError parsing details: {e}")

    parsed_data['notes'] = "\n".join(notes) # Assign notes without tags section

    # REMOVED final debug print
    # print("\nDEBUG: Final parsed_data after multi-row selection and full parse:")
    # import pprint
    # pprint.pprint(parsed_data)
    # print("---\n")

    return parsed_data

# --- ERANK Analysis Helper Functions ---
def parse_erank_text_content(erank_text):
    """
    Parses pasted text content from the ERANK Keyword Tool page using a procedural,
    chunk-based approach.
    Extracts Keyword, Avg. Searches, Avg. Clicks, Avg. CTR, Etsy Competition,
    Google Searches, and the Seed Keyword.
    Returns: tuple (seed_keyword, keywords_data_list) or (None, []) on failure.
    """
    lines = [line.strip() for line in erank_text.strip().splitlines()] # Ensure lines are stripped
    keywords_data = []
    extracted_seed_keyword = None
    seed_keyword_line_index = -1
    data_start_index = -1
    data_end_index = len(lines)

    # --- 1. Find Seed Keyword (Simplified Extraction) --- 
    seed_line_prefix = "Keywords related to"
    # Corrected quote char lists
    quote_chars_open = ["\"", "'", "“"] # Fixed
    quote_chars_close = ["\"", "'", "”"] # Fixed AGAIN
    for i, line in enumerate(lines):
        # REMOVED DEBUG PRINT for seed check
        if line.strip().startswith(seed_line_prefix):
            try:
                # Find first opening quote after prefix
                prefix_end_index = line.find(seed_line_prefix) + len(seed_line_prefix)
                first_quote_index = -1
                for quote in quote_chars_open:
                    idx = line.find(quote, prefix_end_index)
                    if idx != -1:
                        if first_quote_index == -1 or idx < first_quote_index:
                            first_quote_index = idx
                
                # Find last closing quote on the line
                last_quote_index = -1
                for quote in quote_chars_close:
                    idx = line.rfind(quote)
                    if idx != -1 and idx > first_quote_index: # Ensure it's after the first quote
                         if last_quote_index == -1 or idx > last_quote_index:
                             last_quote_index = idx
                
                if first_quote_index != -1 and last_quote_index != -1:
                    keyword_part = line[first_quote_index + 1 : last_quote_index].strip()
                    if keyword_part:
                        extracted_seed_keyword = keyword_part
                        seed_keyword_line_index = i
                        print(f"DEBUG ERANK: Found seed keyword '{extracted_seed_keyword}' at line {i} (Simplified Extraction)")
                        break
                else:
                    print(f"DEBUG ERANK: Found prefix line '{line}', but failed to find start/end quotes.")

            except Exception as e:
                print(f"DEBUG ERANK: Error extracting seed keyword from line '{line}': {e}")
                # Continue searching

    if seed_keyword_line_index == -1:
        print("DEBUG ERANK: Seed keyword line ('Keywords related to...') not found.")
        return None, []

    # --- 2. Find Data Start Marker --- 
    exclude_marker = "EXCLUDE KEYWORDS"
    exclude_marker_found = False
    for i in range(seed_keyword_line_index + 1, len(lines) - 1): # Check up to second-to-last line
        if lines[i] == exclude_marker:
             exclude_marker_found = True
             # Data should start 2 lines after this (skip the 0/5)
             potential_start = i + 2
             if potential_start < len(lines):
                 data_start_index = potential_start
                 print(f"DEBUG ERANK: Found '{exclude_marker}' at line {i}. Setting data start index to {data_start_index}.")
                 break
             else:
                 print(f"DEBUG ERANK: Found '{exclude_marker}' but not enough lines after it.")
                 return extracted_seed_keyword, [] # Cannot proceed

    if data_start_index == -1:
        if exclude_marker_found:
             print(f"DEBUG ERANK: Found '{exclude_marker}' but failed to set data start index.") # Should not happen if check above works
        else:
             print(f"DEBUG ERANK: Data start marker ('{exclude_marker}') not found after seed keyword line.")
        return extracted_seed_keyword, []

    # --- 3. Find Data End Marker --- 
    end_markers = ["Rows per page:", "Copyright ©"]
    for i in range(data_start_index, len(lines)):
        if any(marker in lines[i] for marker in end_markers):
            data_end_index = i
            print(f"DEBUG ERANK: Found end marker '{lines[i]}' at line {i}. Setting data end index.")
            break
    if data_end_index == len(lines):
         print(f"DEBUG ERANK: No explicit end marker found. Parsing until end of text.")

    # --- 4. Process Data Chunks (Expecting 9 lines per keyword) --- 
    current_index = data_start_index
    while current_index < data_end_index:
        # Check if enough lines remain for a potential 9-line chunk
        if current_index + 8 >= data_end_index:
            # REMOVED redundant print - summary below covers it
            # print(f"DEBUG ERANK: Not enough lines remaining for a full chunk starting at {current_index}. Stopping.")
            break

        # --- Define the 9 lines for the current chunk ---
        line1_kw = lines[current_index]       # Keyword
        line2_date = lines[current_index + 1]   # Date (Not used anymore but part of structure)
        line3_trend = lines[current_index + 2]  # Trend
        line4_counts = lines[current_index + 3] # Char Count \t Tag Occurrences
        line5_search = lines[current_index + 4] # Avg Searches
        line6_clicks = lines[current_index + 5] # Avg Clicks
        line7_ctr = lines[current_index + 6]    # Avg CTR
        line8_comp = lines[current_index + 7]   # Etsy Competition
        line9_goog = lines[current_index + 8]   # Google Searches

        # REMOVED detailed chunk processing prints
        # print(f"\nDEBUG ERANK Chunk Processing: Index {current_index}")
        # print(f" L1 Kw: '{line1_kw}'")
        # print(f" L4 Cts: '{line4_counts}'")
        # print(f" L5 Srch: '{line5_search}'")
        # print(f" L6 Clk: '{line6_clicks}'")
        # print(f" L7 CTR: '{line7_ctr}'")
        # print(f" L8 Comp: '{line8_comp}'")
        # print(f" L9 Goog: '{line9_goog}'")

        # --- Basic Validation of Chunk Structure ---
        is_likely_keyword = bool(re.search(r'[a-zA-Z]', line1_kw)) and not line1_kw.replace(' ', '').isdigit()
        has_tab_in_line4 = '\t' in line4_counts
        is_likely_google = bool(re.match(r'^[\d,]+$|^N/A$|^Unknown$', line9_goog, re.IGNORECASE))

        if is_likely_keyword and has_tab_in_line4 and is_likely_google:
            # REMOVED print
            # print("DEBUG ERANK Chunk: Structure looks valid. Extracting...")
            try:
                keyword_entry = {
                    'Keyword': line1_kw,
                    'Avg Searches': line5_search,
                    'Avg Clicks': line6_clicks,
                    'Avg CTR': line7_ctr,
                    'Etsy Competition': line8_comp,
                    'Google Searches': line9_goog
                }
                keywords_data.append(keyword_entry)
                # REMOVED print
                # print(f"DEBUG ERANK Chunk: Success -> {keyword_entry}")
                current_index += 9 # Move to the next potential chunk
            except Exception as e:
                print(f"DEBUG ERANK Chunk: ERROR extracting data from valid-looking chunk at index {current_index}: {e}")
                current_index += 1 
                while current_index < data_end_index and not lines[current_index]: current_index += 1
        else:
            # REMOVED print
            # print(f"DEBUG ERANK Chunk: Invalid structure detected at index {current_index}. Skipping line.")
            # print(f"  (LikelyKeyword: {is_likely_keyword}, TabLine4: {has_tab_in_line4}, LikelyGoog: {is_likely_google})")
            current_index += 1
            while current_index < data_end_index and not lines[current_index]: current_index += 1

    print(f"\nERANK Summary: Parsing loop finished. Extracted {len(keywords_data)} keyword entries.") # Keep summary
    return extracted_seed_keyword, keywords_data

def clean_erank_value(val_str):
    if val_str is None: return np.nan
    val_str = str(val_str).strip().lower().replace(',', '')
    if 'unknown' in val_str or 'n/a' in val_str: return np.nan
    if '<' in val_str:
        num_part = re.search(r'< ?(\d+(\.\d+)?)', val_str)
        # Return slightly less than the threshold for '< X' values
        return float(num_part.group(1)) - 0.01 if num_part else 1.0 # Adjusted fallback
    val_str = val_str.replace('%', '')
    try: return float(val_str)
    except ValueError: return np.nan

# --- New Absolute Scoring Functions (Replacing Normalization) ---
def score_searches(value):
    if pd.isna(value): return 0.5 # Neutral for missing
    if value < 50: return 0.1
    if value < 250: return 0.3
    if value < 1000: return 0.6
    if value < 5000: return 0.8
    return 1.0

def score_ctr(value):
    if pd.isna(value): return 0.5 # Neutral for missing
    # Assumes value is a percentage e.g. 99.0
    if value < 50: return 0.1
    if value < 75: return 0.4
    if value < 95: return 0.7
    return 1.0

def score_competition(value):
    if pd.isna(value): return 0.5 # Neutral for missing
    # Lower competition gets higher score
    if value < 1000: return 1.0
    if value < 20000: return 0.8
    if value < 100000: return 0.5
    if value < 500000: return 0.2
    return 0.0 # >= 500k

# --- Validation Helpers ---
def validate_float(val_str, field_name):
    if not val_str: return None, True
    try: return float(str(val_str).replace(',', '').replace('$', '').replace('£', '').replace('€', '')), True
    except ValueError: st.warning(f"Invalid {field_name} format."); return None, False

def validate_int(val_str, field_name):
    if not val_str: return None, True
    try: return int(str(val_str).replace(',', '')), True
    except ValueError: st.warning(f"Invalid {field_name} format."); return None, False

# --- Session State Initialization ---
def init_session_state():
    # === Opportunity Tracker Fields ===
    string_fields_opp = [
        "product_title", "product_url", "shop_name", "shop_url",
        "price_str", "processing_time", "est_revenue_str",
        "est_sales_str", "shop_age", "niche_tags", "aliexpress_urls", "notes",
        "pasted_html", "shipping_cost_str",
        "total_sales_str", "views_str", "favorites_str", "conversion_rate",
        "listing_age", "shop_age_overall", "category", "visibility_score",
        "review_ratio", "monthly_reviews_str", "listing_type",
        "last_30_days_sales_str", "last_30_days_revenue_str",
        "pasted_everbee_text",
        # Keys for opportunity form inputs to avoid conflicts
        "opp_form_product_title", "opp_form_product_url", "opp_form_shop_name",
        "opp_form_shop_url", "opp_form_price_str", "opp_form_processing_time",
        "opp_form_shipping_cost_str", "opp_form_est_revenue_str", "opp_form_est_sales_str",
        "opp_form_last_30_days_sales_str", "opp_form_last_30_days_revenue_str",
        "opp_form_listing_age", "opp_form_shop_age_overall", "opp_form_category",
        "opp_form_niche_tags", "opp_form_total_sales_str", "opp_form_views_str",
        "opp_form_favorites_str", "opp_form_conversion_rate", "opp_form_listing_type",
        "opp_form_aliexpress_urls", "opp_form_notes"
    ]
    for field in string_fields_opp:
        if field not in st.session_state: st.session_state[field] = ""
    if "is_digital" not in st.session_state: st.session_state["is_digital"] = False # For opp form
    if 'tags_list' not in st.session_state: st.session_state['tags_list'] = [] # Everbee tags for current opp
    if 'etsy_price_float' not in st.session_state: st.session_state['etsy_price_float'] = None
    if 'delete_id_input' not in st.session_state: st.session_state['delete_id_input'] = None

    # === ERANK Analysis Fields ===
    if 'erank_keywords_list' not in st.session_state: st.session_state['erank_keywords_list'] = [] # Stores SCORED list for CURRENT session display
    if 'raw_erank_data' not in st.session_state: st.session_state['raw_erank_data'] = [] # Stores RAW parsed list for SAVING
    if 'pasted_erank_text' not in st.session_state: st.session_state['pasted_erank_text'] = ""
    if 'erank_seed_keyword' not in st.session_state: st.session_state['erank_seed_keyword'] = ""
    if 'w_searches' not in st.session_state: st.session_state['w_searches'] = 0.4
    if 'w_ctr' not in st.session_state: st.session_state['w_ctr'] = 0.3
    if 'w_comp' not in st.session_state: st.session_state['w_comp'] = 0.3

init_session_state()

# --- App Layout ---
st.set_page_config(page_title="Etsy Research Hub", page_icon="🎯", layout="wide")
st.title("🎯 Etsy Research Hub")
st.caption("Track opportunities and analyze ERANK keywords.")

# --- TABS ---
tab1, tab2 = st.tabs(["Opportunity Tracker", "ERANK Keyword Analysis"])

# =========================== #
# === Opportunity Tracker Tab === #
# =========================== #
with tab1:
    st.header("Opportunity Tracker Workflow")

    # --- Prompt Generation Section ---
    with st.expander("Generate Keyword Research Prompt", expanded=False):
        st.subheader("Generate Keyword Research Prompt")
        prompt_col1, prompt_col2 = st.columns(2)
        with prompt_col1: st.text_input("Niche Category", key="prompt_niche", placeholder="E.g., Ceramic Kitchenware")
        with prompt_col2: st.text_input("Style or Type Focus", key="prompt_style", placeholder="E.g., Moroccan")
        current_niche = st.session_state.prompt_niche
        current_style = st.session_state.prompt_style
        prompt_template = f""" # Use your full prompt template here """
        st.text_area("Generated Prompt (for Copying)", value=prompt_template, height=200)

    # --- Keyword Research Section ---
    with st.expander("1. Keyword Research Links", expanded=True):
        st.subheader("Enter Keywords for Etsy Search")
        min_price_filter = st.number_input("Minimum Price (£)", min_value=0, value=25, step=1, key="opp_min_price_filter")
        keywords_input = st.text_area("Enter keywords (one per line):", height=150, placeholder="ceramic mug...", key="opp_kw_input")
        if keywords_input:
            keywords = [kw.strip() for kw in keywords_input.split('\n') if kw.strip()]
            st.subheader("Clickable Etsy Search Links (Best Sellers > £" + str(min_price_filter) + ")")
            urls_to_open = [generate_etsy_url(kw, min_price=min_price_filter) for kw in keywords]
            for i, kw in enumerate(keywords): st.markdown(f"- [{kw}]({urls_to_open[i]})", unsafe_allow_html=True)
            if st.button("🚀 Open All Links in New Tabs", key="open_etsy_tabs"):
                count = 0
                with st.spinner(f"Opening {len(urls_to_open)} tabs..."):
                    for url in urls_to_open:
                        try: webbrowser.open_new_tab(url)
                        except Exception as web_e: st.warning(f"Failed to open {url}: {web_e}"); continue
                        count += 1; time.sleep(0.5) # Short delay
                st.success(f"Opened {count} tabs.")

    # --- Opportunity Database Section ---
    st.header("2. Capture Opportunity Data")

    with st.expander("Parse Etsy Product Page HTML", expanded=True):
        st.subheader("Parse Product Page HTML")
        st.info("Go to the Etsy product page, Ctrl+A, Ctrl+C, and paste below.")
        pasted_html_input = st.text_area("Paste Full HTML Content Here:", height=200, key='pasted_html') # Key matches state init
        if st.button("Parse Pasted HTML", key="parse_html_button"):
            if st.session_state.pasted_html:
                with st.spinner("Parsing HTML..."):
                    try:
                        parsed_data = parse_etsy_html_content(st.session_state.pasted_html)
                        # Update session state from parsed Etsy data
                        st.session_state.opp_form_product_title = parsed_data.get('product_title', '')
                        st.session_state.opp_form_product_url = parsed_data.get('product_url', '')
                        st.session_state.opp_form_price_str = parsed_data.get('price_str', '')
                        st.session_state.opp_form_shop_name = parsed_data.get('shop_name', '')
                        st.session_state.opp_form_shop_url = parsed_data.get('shop_url', '')
                        st.session_state.opp_form_processing_time = parsed_data.get('processing_time', '')
                        st.session_state.opp_form_shipping_cost_str = parsed_data.get('shipping_cost_str', '')
                        # Update the float price for calculations
                        if st.session_state.opp_form_price_str:
                            try: st.session_state.etsy_price_float = float(re.sub(r'[^\d.]', '', st.session_state.opp_form_price_str))
                            except ValueError: st.session_state.etsy_price_float = None
                        else: st.session_state.etsy_price_float = None
                        # Append notes
                        notes_key = 'opp_form_notes' # Use the form key
                        existing_notes = st.session_state[notes_key]
                        desc = parsed_data.get('description_notes')
                        revs = parsed_data.get('review_dates_str')
                        if desc and "--- Description ---" not in existing_notes: existing_notes += f"\n\n--- Description ---\n{desc}"
                        if revs and "--- Review Dates ---" not in existing_notes: existing_notes += f"\n\n--- Review Dates (YYYY-MM-DD) ---\n{revs}"
                        st.session_state[notes_key] = existing_notes.strip()
                        st.success("HTML Parsed and form fields updated!")
                    except Exception as e: st.error(f"Error parsing HTML: {e}")
            else: st.warning("Please paste HTML content.")

    with st.expander("Parse Everbee Page Text", expanded=True):
        st.subheader("Parse Everbee Page Text")
        st.warning("⚠️ Everbee text parsing is fragile.")
        st.info("Paste full text content from Everbee page below.")
        pasted_everbee_input = st.text_area("Paste Full Everbee Page Text Content Here:", height=200, key='pasted_everbee_text')
        if st.button("Parse Everbee Text", key="parse_everbee_button"):
            if st.session_state.pasted_everbee_text:
                with st.spinner("Parsing Everbee text..."):
                    try:
                        parsed_data = parse_everbee_text_content(st.session_state.pasted_everbee_text)
                        if parsed_data:
                           # Update relevant form fields in session state
                            st.session_state.opp_form_product_title = parsed_data.get('product_title', st.session_state.opp_form_product_title) # Allow override if needed
                            st.session_state.opp_form_shop_name = parsed_data.get('shop_name', st.session_state.opp_form_shop_name)
                            st.session_state.opp_form_est_sales_str = str(parsed_data.get('monthly_sales', ''))
                            st.session_state.opp_form_est_revenue_str = parsed_data.get('monthly_revenue_str_display', str(parsed_data.get('monthly_revenue', '')))
                            st.session_state.opp_form_total_sales_str = str(parsed_data.get('total_sales', ''))
                            st.session_state.opp_form_views_str = str(parsed_data.get('views', ''))
                            st.session_state.opp_form_favorites_str = str(parsed_data.get('favorites', ''))
                            st.session_state.opp_form_conversion_rate = parsed_data.get('conversion_rate', '')
                            st.session_state.opp_form_listing_age = parsed_data.get('listing_age', '')
                            st.session_state.opp_form_shop_age_overall = parsed_data.get('shop_age_overall', '')
                            st.session_state.opp_form_category = parsed_data.get('category', '')
                            st.session_state.opp_form_listing_type = parsed_data.get('listing_type', '')
                            st.session_state.tags_list = parsed_data.get('tags_list', []) # Update tags list
                            st.session_state.opp_form_last_30_days_sales_str = parsed_data.get('last_30_days_sales', '')
                            # Calculate 30d Revenue using ETSY PRICE float
                            price_val = st.session_state.get('etsy_price_float')
                            sales_30d_str = parsed_data.get('last_30_days_sales')
                            if price_val is not None and sales_30d_str:
                                try:
                                    sales_30d_int = int(str(sales_30d_str).replace(',', ''))
                                    st.session_state.opp_form_last_30_days_revenue_str = f"{(price_val * sales_30d_int):.2f}"
                                except: st.session_state.opp_form_last_30_days_revenue_str = "Error"
                            else: st.session_state.opp_form_last_30_days_revenue_str = ""
                            # Append notes
                            notes_key = 'opp_form_notes'
                            existing_notes = st.session_state[notes_key]
                            eb_notes = parsed_data.get('notes', '') # Get notes from parser
                            if eb_notes and "--- Everbee" not in existing_notes: # Avoid duplicates
                                existing_notes += f"\n\n{eb_notes}"
                            st.session_state[notes_key] = existing_notes.strip()
                            st.success("Everbee text parsed and form fields updated!")
                        else: st.warning("Everbee parsing failed.")
                    except Exception as e: st.error(f"Error parsing Everbee text: {e}")
            else: st.warning("Please paste Everbee text.")

    # --- Manual Entry / Review Form ---
    with st.expander("Add/Review Opportunity Details", expanded=True):
        st.subheader("Product/Shop Data")
        col1, col2 = st.columns(2)
        # Use the unique keys defined in init_session_state for all inputs here
        with col1:
            st.text_input("Product Title", key="opp_form_product_title")

    # --- Saved Opportunities Display ---
    st.header("3. Saved Opportunities")
    with st.expander("Delete Opportunity", expanded=False):
        # ... (Keep delete logic) ...
        del_col1, del_col2 = st.columns([1, 3])
        with del_col1: id_to_delete = st.number_input("Enter ID to Delete", min_value=1, step=1, value=None, key="delete_id_input")
        with del_col2:
            st.caption(" "); st.caption(" ") # Vertical space
            if st.button("🗑️ Delete Opportunity by ID", key="delete_button"):
                if id_to_delete:
                    with st.spinner(f"Deleting ID: {id_to_delete}..."):
                        if db.delete_opportunity_by_id(id_to_delete): st.success(f"Deleted ID: {id_to_delete}"); st.rerun()
                        else: st.error(f"Failed to delete ID: {id_to_delete}.")
                else: st.warning("Please enter an ID.")

    # --- Fetch and Display Opportunities --- 
    opportunities_df = db.get_all_opportunities() # Moved this line UP
    if opportunities_df.empty:
        st.info("No opportunities saved yet.")
    else:
        filter_col1, filter_col2 = st.columns([1, 3]); # Basic Filtering
        # ... (rest of filtering and display logic remains the same) ...


# ============================ #
# === ERANK Analysis Tab === #
# ============================ #
with tab2:
    st.header("ERANK Keyword Analysis")
    st.info("Analyze keywords copied from ERANK's Keyword Tool.")

    # --- Weight Inputs ---
    st.markdown("**Opportunity Score Weights:**")
    w_col1, w_col2, w_col3 = st.columns(3)
    with w_col1: st.number_input("Avg. Searches Weight", 0.0, 1.0, step=0.05, key="w_searches", help="Importance of search volume (0-1)")
    with w_col2: st.number_input("Avg. CTR Weight", 0.0, 1.0, step=0.05, key="w_ctr", help="Importance of click-through rate (0-1)")
    with w_col3: st.number_input("Low Competition Weight", 0.0, 1.0, step=0.05, key="w_comp", help="Importance of *low* competition (0-1)")

    # --- Input Area ---
    st.text_input("Seed Keyword (auto-detected, editable)", key="erank_seed_keyword")
    pasted_erank_text_input = st.text_area("Paste Full ERANK Page Text Content Here:", height=250, key='pasted_erank_text')

    # --- Analysis Button --- 
    if st.button("Analyze Pasted ERANK Text", key="analyze_erank_button"):
        if st.session_state.pasted_erank_text:
            with st.spinner("Parsing and Analyzing ERANK text..."):
                try:
                    # 1. Parse Raw Data - returns seed keyword, raw_data_list
                    extracted_seed_keyword, parsed_erank_data = parse_erank_text_content(st.session_state.pasted_erank_text)
                    
                    # Store RAW data for potential saving
                    st.session_state['raw_erank_data'] = parsed_erank_data
                    
                    # Update the seed keyword input field (read-only update via info msg)
                    if extracted_seed_keyword:
                        st.info(f"Auto-detected seed keyword: '{extracted_seed_keyword}'")
                    else:
                        st.info("Could not auto-detect seed keyword from text.") 

                    if parsed_erank_data:
                        # Process a COPY for current session display
                        erank_df = pd.DataFrame(parsed_erank_data.copy()) 
                        st.info(f"ERANK text parsed! Found {len(erank_df)} raw keyword entries. Analyzing for current view...")
                        
                        # --- Apply Scoring for Current View --- 
                        erank_df['Searches_Num'] = erank_df['Avg Searches'].apply(clean_erank_value)
                        erank_df['CTR_Num'] = erank_df['Avg CTR'].apply(clean_erank_value)
                        erank_df['Competition_Num'] = erank_df['Etsy Competition'].apply(clean_erank_value)
                        erank_df['Searches_Score'] = erank_df['Searches_Num'].apply(score_searches)
                        erank_df['CTR_Score'] = erank_df['CTR_Num'].apply(score_ctr)
                        erank_df['Competition_Score'] = erank_df['Competition_Num'].apply(score_competition)
                        
                        current_w_searches = st.session_state.w_searches
                        current_w_ctr = st.session_state.w_ctr
                        current_w_comp = st.session_state.w_comp

                        # Calculate final score using absolute scores and weights
                        erank_df['Opportunity Score'] = (
                            (current_w_searches * erank_df['Searches_Score']) +
                            (current_w_ctr * erank_df['CTR_Score']) +
                            (current_w_comp * erank_df['Competition_Score'])
                        )
                        # --- End Scoring --- 
                        
                        erank_df_sorted = erank_df.sort_values(by='Opportunity Score', ascending=False, na_position='last')
                        # Store SCORED data for current session display
                        st.session_state['erank_keywords_list'] = erank_df_sorted.to_dict('records')
                        st.success(f"ERANK data analyzed and scored for current view!")
                    else:
                        st.warning("ERANK parsing failed to extract keyword data.")
                        st.session_state['erank_keywords_list'] = []
                        st.session_state['raw_erank_data'] = [] # Clear raw data too
                except Exception as e:
                    st.error(f"ERANK Analysis Error: {e}")
                    st.session_state['erank_keywords_list'] = []
                    st.session_state['raw_erank_data'] = [] # Clear raw data on error
        else: 
            st.warning("Please paste ERANK text.")

    # --- Display Analyzed ERANK data (Current Session) ---
    st.subheader("Analyzed Keywords (Current Session)")
    if st.session_state.get('erank_keywords_list'):
        erank_display_df_current = pd.DataFrame(st.session_state['erank_keywords_list'])
        display_columns_current = [
            'Opportunity Score', 'Keyword', 'Avg Searches', 'Avg CTR', # Removed Data Date - not needed for current session?
            'Etsy Competition', 'Avg Clicks', 'Google Searches'
        ]
        # Filter columns to display safely
        erank_display_df_current = erank_display_df_current[[col for col in display_columns_current if col in erank_display_df_current.columns]]
        
        # Format Opportunity Score
        if 'Opportunity Score' in erank_display_df_current.columns:
             try: 
                 erank_display_df_current['Opportunity Score'] = pd.to_numeric(erank_display_df_current['Opportunity Score'], errors='coerce').map(lambda x: f'{x:.3f}' if pd.notna(x) else 'N/A')
             except Exception as fmt_e: 
                 print(f"Warning: Could not format Score for current session display: {fmt_e}")
                 
        st.dataframe(erank_display_df_current, use_container_width=True, hide_index=True)

        # --- Save Button --- 
        if st.button("💾 Save Current Raw Keyword Data", key="save_erank_button"):
            current_raw_data = st.session_state.get('raw_erank_data', [])
            current_weights = {'w_searches': st.session_state.w_searches, 'w_ctr': st.session_state.w_ctr, 'w_comp': st.session_state.w_comp}
            # Get seed keyword from the input box at the time of saving
            seed_keyword = st.session_state.erank_seed_keyword or None 
            
            if current_raw_data:
                # Call updated db function with RAW data
                saved_analysis_id = db.add_erank_analysis(seed_keyword, current_weights, current_raw_data)
                if saved_analysis_id:
                     st.success(f"Saved analysis metadata (ID: {saved_analysis_id}) and {len(current_raw_data)} raw keywords.")
                else: 
                    st.error("Failed to save ERANK analysis metadata and keywords.")
            else: 
                st.warning("No raw keyword data from current session to save.")
    else:
        st.info("Paste ERANK data above and click Analyze.")

    # --- Display ALL Saved Keywords with Global Scoring --- 
    st.divider()
    st.subheader("Global Keyword Analysis (All Saved Raw Data)")
    
    all_keywords_df = db.get_all_erank_keywords()
    
    if all_keywords_df.empty:
        st.info("No keywords saved to the database yet. Save data from a session above.")
    else:
        st.info(f"Found {len(all_keywords_df)} total saved keywords. Applying current weights for global ranking...")
        with st.spinner("Analyzing all saved keywords..."):
            try:
                # Create a copy to avoid modifying the original DataFrame from DB
                # Use .loc for modifications to avoid SettingWithCopyWarning
                df_processed = all_keywords_df.copy()
                
                # --- Apply Scoring to ALL Keywords --- 
                df_processed.loc[:, 'Searches_Num'] = df_processed['Avg Searches'].apply(clean_erank_value)
                df_processed.loc[:, 'CTR_Num'] = df_processed['Avg CTR'].apply(clean_erank_value)
                df_processed.loc[:, 'Competition_Num'] = df_processed['Etsy Competition'].apply(clean_erank_value)
                df_processed.loc[:, 'Searches_Score'] = df_processed['Searches_Num'].apply(score_searches)
                df_processed.loc[:, 'CTR_Score'] = df_processed['CTR_Num'].apply(score_ctr)
                df_processed.loc[:, 'Competition_Score'] = df_processed['Competition_Num'].apply(score_competition)
                
                current_w_searches_global = st.session_state.w_searches
                current_w_ctr_global = st.session_state.w_ctr
                current_w_comp_global = st.session_state.w_comp
                
                df_processed.loc[:, 'Opportunity Score'] = (
                    (current_w_searches_global * df_processed['Searches_Score']) +
                    (current_w_ctr_global * df_processed['CTR_Score']) +
                    (current_w_comp_global * df_processed['Competition_Score'])
                )
                
                df_sorted_global = df_processed.sort_values(by='Opportunity Score', ascending=False, na_position='last')
                
                display_columns_global = [
                    'Opportunity Score', 'Keyword', 'Added At', 'Avg Searches', 'Avg CTR', 
                    'Etsy Competition', 'Avg Clicks', 'Google Searches', 
                    'analysis_id', 'keyword_id' 
                ]
                df_display_global = df_sorted_global[[col for col in display_columns_global if col in df_sorted_global.columns]].copy() # Create copy for display formatting
                
                # REMOVED DEBUG print for unique dates
                # if 'Added At' in df_display_global.columns:
                #     try:
                #         unique_dates = df_display_global['Added At'].unique()
                #         print(f"DEBUG APP: Unique 'Added At' values BEFORE conversion: {unique_dates}")
                #     except Exception as e:
                #         print(f"DEBUG APP: Error getting unique dates: {e}")

                # Format Opportunity Score and Added At date (using .loc)
                if 'Opportunity Score' in df_display_global.columns:
                    try: 
                        df_display_global.loc[:, 'Opportunity Score'] = pd.to_numeric(df_display_global['Opportunity Score'], errors='coerce').map(lambda x: f'{x:.3f}' if pd.notna(x) else 'N/A')
                    except Exception as fmt_e: 
                        print(f"Warning: Could not format Score for global display: {fmt_e}")
                if 'Added At' in df_display_global.columns:
                    try:
                        # Use .loc; Removed deprecated infer_datetime_format=True
                        df_display_global.loc[:, 'Added At'] = pd.to_datetime(df_display_global['Added At'], errors='coerce').dt.strftime('%Y-%m-%d') 
                    except Exception as fmt_e:
                        print(f"Warning: Could not format Added At date for global display: {fmt_e}")
                
                st.dataframe(df_display_global, use_container_width=True, hide_index=True, height=600)

            except Exception as e:
                st.error(f"Error analyzing all saved keywords: {e}")

    # --- Display Past Analysis Metadata (Optional) ---
    with st.expander("View Past Analysis Session Metadata", expanded=False):
        past_analyses_df = db.get_all_erank_analyses()
        if past_analyses_df.empty: 
            st.info("No past analysis metadata saved.")
        else:
            st.dataframe(
                past_analyses_df[['id', 'analyzed_at', 'seed_keyword', 'weights']], # Show subset
                column_config={ "id": "Analysis ID", "analyzed_at": "Date", "seed_keyword": "Seed", "weights": "Weights (JSON)" },
                hide_index=True, use_container_width=True
            )