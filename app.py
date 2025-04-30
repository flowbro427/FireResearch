import streamlit as st
import pandas as pd
import database as db
import json # Added json library
from urllib.parse import quote_plus, urlparse, urlunparse
from bs4 import BeautifulSoup # Added BeautifulSoup
from datetime import datetime, date # Added date
import re # Added re for regular expressions
import webbrowser # Added webbrowser to open tabs
import time # Added time for delays
import subprocess # Added subprocess for specific browser control
import platform # Added platform to detect OS

# --- Initialization --- 
# Ensure DB is initialized early before any potential access
db.initialize_db()

# --- Page Configuration ---
st.set_page_config(
    page_title="Etsy Opportunity Tracker",
    page_icon="🎯",
    layout="wide"
)

# --- Session State Initialization ---
# Initialize session state keys if they don't exist
def init_session_state():
    # Initialize non-boolean fields first
    string_fields = [
        "product_title", "product_url", "shop_name", "shop_url",
        "price_str", "processing_time", "est_revenue_str",
        "est_sales_str", "shop_age", "niche_tags", "aliexpress_urls", "notes",
        "pasted_html", "shipping_cost_str",
        # New Everbee fields (using _str suffix for display where needed)
        "total_sales_str", "views_str", "favorites_str", "conversion_rate",
        "listing_age", "shop_age_overall", "category", "visibility_score",
        "review_ratio", "monthly_reviews_str", "listing_type"
    ]
    for field in string_fields:
        if field not in st.session_state:
            st.session_state[field] = "" # Initialize as empty string

    # Initialize boolean fields
    if "is_digital" not in st.session_state:
        st.session_state["is_digital"] = False # Initialize as False
        
    # Initialize tags list
    if 'tags_list' not in st.session_state:
        st.session_state['tags_list'] = [] # Initialize as empty list

init_session_state()

# --- Helper Functions ---
def generate_etsy_url(keyword, min_price=25):
    """Generates a clickable Etsy search URL for a given keyword, filtering for bestsellers above a min price (UK site)."""
    if not keyword:
        return ""
    query = quote_plus(keyword) # URL-encode the keyword
    # Construct the URL with the new parameters
    return f"https://www.etsy.com/uk/search?q={query}&ref=search_bar&explicit=1&custom_price=1&min={min_price}&is_best_seller=true"

def clean_etsy_url(url):
    """Removes query parameters and fragments from a URL."""
    if not url or '?' not in url:
        return url
    # Simpler approach: just split at '?' and take the first part
    return url.split('?')[0]

def calculate_days_until_delivery(date_str):
    """Calculates days from today until the estimated delivery date/range."""
    if not date_str:
        return ""

    today = date.today()
    current_year = today.year
    time_str = ""

    # Regex to find date patterns like "30 Apr" or "30 Apr-06 May"
    match = re.search(r'(\d{1,2}\s+[A-Za-z]{3})(?:-(\d{1,2}\s+[A-Za-z]{3}))?', date_str)
    if not match:
        return date_str # Return original string if format not recognized

    start_date_str = match.group(1)
    end_date_str = match.group(2)

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

    if end_date_str:
        end_date = parse_date_with_year(end_date_str)
        if end_date:
            # Ensure end date is not before start date (handles year rollover)
            if end_date < start_date:
                 end_date = datetime.strptime(f"{end_date_str} {start_date.year + 1}", '%d %b %Y').date()

            delta_end = (end_date - today).days
            if delta_start >= 0 and delta_end >= 0:
                time_str = f"{delta_start}-{delta_end} days"
            else: # If calculated range is invalid, return original
                 time_str = date_str
        else:
             time_str = date_str # Return original if end date fails
    else:
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
                est_delivery_days = calculate_days_until_delivery(date_range_str)
            else:
                # Fallback text search if specific span not found within edd_li
                 arrival_tag = edd_li.find(string=lambda t: t and ('get it by' in t.lower() or 'arrives by' in t.lower()))
                 if arrival_tag:
                     date_part = arrival_tag.strip().split('by')[-1].strip()
                     est_delivery_days = calculate_days_until_delivery(date_part)
        
        # --- If original method failed, broaden the search within the section ---
        if not est_delivery_days:
            # Search for any text node containing "Get it by" or "Arrives by" within the shipping section
            arrival_text_match = shipping_section.find(string=lambda t: t and ('get it by' in t.lower() or 'arrives by' in t.lower()))
            if arrival_text_match:
                # Extract the date part after "by"
                try:
                    date_part = arrival_text_match.strip().split('by')[-1].strip()
                    # Remove potential leading/trailing punctuation if necessary (e.g., if it finds " Arrives by **30 Apr**.")
                    date_part = re.sub(r'^[^\\w\\d]+|[^\\w\\d]+$', '', date_part) # Basic cleanup
                    if date_part: # Ensure we have something left after cleaning
                        est_delivery_days = calculate_days_until_delivery(date_part)
                except IndexError:
                    pass # Ignore if split fails

        # --- Shipping Cost (Existing logic seems okay) ---
        # Find any element containing "Delivery cost:" (case-insensitive)
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

    data['processing_time'] = est_delivery_days # Store calculated days
    data['shipping_cost_str'] = shipping_cost_str # Store found cost

    # Clean up None values to empty strings before returning
    for key in data:
        if data[key] is None:
            data[key] = ""

    return data

# --- Helper function to parse Everbee text content (Refactored for multi-row selection) ---
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
    raw_values_full = {} # For parsing the rest of the fields from the full text

    # --- Define Keyword -> Value Pattern Mappings (Full Set) --- 
    keyword_patterns_full = {
        # Primary Listing Details (May overlap with essential, but parsed again for consistency)
        'Mo. Sales': r'^(\d+)$|', # Already got from best row
        'Mo. Revenue': r'^([\$\£€]\d[\d,\.]*)$|', # Already got from best row
        'Total sales': r'^(\d+)$|',
        'Listing age': r'^(\d+\s+months?)$|',
        'Reviews': r'^(\d+)$|',
        'Views': r'^(\d[\d,]*)$|',
        'Favorites': r'^(\d[\d,]*)$|',
        'Mo. Reviews': r'^(\d+)$|',
        'Conversion rate': r'^([\d.]+%?)$|',
        'Category': r'^([A-Za-z][A-Za-z &/\s]+[A-Za-z])$|', # More specific: start/end with letter
        'Visibility score': r'^(\d+%?)$|',
        'Review ratio': r'^([\d.]+%?)$|',
        'Shop Age': r'^(\d+\s+Mo\.)\s*$', 
    }
    standalone_value_keys_full = ['Shop Age'] 

    # --- Iterate through *ALL* lines looking for Keywords & Values for the full dataset ---
    for i, line in enumerate(lines):
        # Check for standalone values first
        for key in standalone_value_keys_full:
             if key not in raw_values_full:
                 match = re.match(keyword_patterns_full[key], line, re.IGNORECASE)
                 if match and match.group(1):
                     raw_values_full[key] = match.group(1).strip()
                     # Try to find Total Shop Sales and Listing Type nearby
                     if key == 'Shop Age':
                          if i + 2 < num_lines:
                               if 'total_shop_sales' not in raw_values_full and re.match(r'^\d[\d,]*$', lines[i+1]):
                                    raw_values_full['total_shop_sales'] = lines[i+1]
                               if i + 3 < num_lines and 'listing_type' not in raw_values_full and re.match(r'^[A-Za-z]+$', lines[i+2]):
                                     raw_values_full['listing_type'] = lines[i+2]
                     break 
        
        # Check for Keyword labels
        matched_label = None
        for label, value_pattern in keyword_patterns_full.items():
            if line.lower() == label.lower():
                 matched_label = label
                 break
        
        if matched_label and matched_label not in raw_values_full and i + 1 < num_lines:
            value_pattern = keyword_patterns_full[matched_label]
            match_next = re.match(value_pattern, lines[i+1], re.IGNORECASE)
            if match_next and match_next.group(1):
                 raw_values_full[matched_label] = match_next.group(1).strip()
            elif i + 2 < num_lines and not lines[i+1]: # Allow blank line
                 match_skip = re.match(value_pattern, lines[i+2], re.IGNORECASE)
                 if match_skip and match_skip.group(1):
                     raw_values_full[matched_label] = match_skip.group(1).strip()

    # --- Add fully parsed data to the best_row data, converting types --- 
    # Only add if not already present from the essential parse
    parsed_data['category'] = raw_values_full.get('Category')
    if 'listing_age' not in parsed_data: parsed_data['listing_age'] = raw_values_full.get('Listing age')
    parsed_data['shop_age_overall'] = raw_values_full.get('Shop Age')
    if 'listing_type' not in parsed_data: parsed_data['listing_type'] = raw_values_full.get('listing_type')

    # Numeric Conversions for full data
    def safe_int_full(key): 
        val_str = raw_values_full.get(key)
        return int(val_str.replace(',', '')) if val_str else None
    
    parsed_data['total_sales'] = safe_int_full('Total sales')
    parsed_data['reviews_count'] = safe_int_full('Reviews')
    parsed_data['views'] = safe_int_full('Views')
    parsed_data['favorites'] = safe_int_full('Favorites')
    parsed_data['monthly_reviews'] = safe_int_full('Mo. Reviews')
    parsed_data['total_shop_sales'] = safe_int_full('total_shop_sales')

    # Percentage/String fields
    parsed_data['conversion_rate'] = raw_values_full.get('Conversion rate')
    parsed_data['visibility_score'] = raw_values_full.get('Visibility score')
    parsed_data['review_ratio'] = raw_values_full.get('Review ratio')

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

# --- App Layout ---
st.title("🎯 Etsy Opportunity Tracker")
st.caption("Track and validate potential Etsy dropshipping products and niches.")

# --- Prompt Generation Section --- 
st.header("Generate Keyword Research Prompt")

# Initialize session state for prompt inputs if they don't exist
# if 'prompt_niche' not in st.session_state: st.session_state.prompt_niche = "Ceramic Kitchenware"
# if 'prompt_style' not in st.session_state: st.session_state.prompt_style = "Moroccan, Decorative, Stoneware, Earthy"

# Use placeholder text instead of pre-filling value
if 'prompt_niche' not in st.session_state: st.session_state.prompt_niche = "" # Ensure key exists but is empty initially
if 'prompt_style' not in st.session_state: st.session_state.prompt_style = ""

prompt_col1, prompt_col2 = st.columns(2)
with prompt_col1:
    st.text_input("Niche Category", key="prompt_niche", placeholder="E.g., Ceramic Kitchenware")
with prompt_col2:
    st.text_input("Style or Type Focus", key="prompt_style", placeholder="E.g., Moroccan, Decorative, Stoneware, Earthy")

# --- Construct the Prompt --- 
# Read directly from session state keys, which are updated by the inputs
current_niche = st.session_state.prompt_niche
current_style = st.session_state.prompt_style

prompt_template = f"""
You are an Etsy product market research specialist.

Your role is to find high-margin, high-demand Etsy niches where the typical product sells for $30 or more.

Allowed Sources:

Real consumer chatter (Twitter/X, Pinterest, Reddit, TikTok)

Specifically when you search on X, don't forget to look for trends and hashtags that are trending related to the niche category and style focus. Also search for the term '[Niche Category] Ideas' to find new and emerging trends related to the niche category. Search a MINIMUM of 100 X posts.

Official trend and data reports (Etsy trend reports, Google Trends, Pinterest Predicts, marketplace research)

Forbidden Sources:

Blog listicles

SEO-generated articles

AI content farms

Constraints:

Only scalable, repeatable products — no personalized, no handmade-only goods.

Focus only on products typically selling for $30+.

⚙️ Input Parameters:

NICHE CATEGORY: {current_niche}

STYLE or TYPE FOCUS: {current_style}

📝 Output Instructions:

✨ Section 1: Raw Keywords Block (Pure Copy-Paste Format)

Select keywords by conceptually grouping them into three categories for research purposes: Top Broad Title Keywords, Specific Long-Tail Title Keywords, and Emerging Trend Title Keywords.
However, in the final output, do not include these category headings—just present all keywords in a single continuous list.
List all keywords (from all categories combined) one per line in a single block under the "Section 1: Raw Keywords Block" heading.
Ensure each keyword is on its own line in the rendered output by adding a double space at the end of each line in markdown (e.g., keyword  ).
Do not use commas, bullets, numbers, hyphens, or extra spaces after the keywords.
Do not wrap keywords into a paragraph—each keyword must visually appear on a new line in the rendered output.
Do not include empty lines between keywords.
Include at least 20 keyword phrases total (combined across all conceptual categories).
⬇️ Section 1 Example (must match this exactly):

```text
Section 1: Raw Keywords Block  
gold hoop earrings  
chunky knit blanket  
modern wall art  
large gold hoop earrings for women  
chunky knit oversized blanket handmade  
modern abstract wall art canvas print  
organic cotton bathrobe  
moss agate statement ring  
mid century modern coffee table  
```

✨ Section 2: Keyword Commentary and Thoughts

After the keywords block, explain why each keyword group is strong.

Strict commentary formatting:

Start each bullet with the keyword in Bold, then a colon :, followed by a short 1-sentence insight (max 20 words).
Do not use paragraph writing.
Do not wrap onto a second line.
Group commentary by:
Top Broad Title Keywords
Specific Long-Tail Title Keywords
Emerging Trend Title Keywords
⬇️ Section 2 Example (must match this format):

```text
Top Broad Title Keywords  
- **Gold hoop earrings**: High-demand jewelry staple, consistent $30–$80 price bracket.  
- **Chunky knit blanket**: Strong seasonal sales, high perceived value, perfect for gifting.  
- **Modern wall art**: Evergreen decor category, wide buyer base, easy pricing $40–$200.  

Specific Long-Tail Title Keywords  
- **Large gold hoop earrings for women**: Broad appeal with strong style demand at good margin.  
- **Chunky knit oversized blanket handmade**: Premium cozy item, winter trending.  
- **Modern abstract wall art canvas print**: High average order value home upgrade.  

Emerging Trend Title Keywords  
- **Organic cotton bathrobe**: Sustainability trend scaling fast.  
- **Moss agate statement ring**: Crystal jewelry boom among Gen Z.  
- **Mid century modern coffee table**: 1950s revival trending in furniture.  
```

❗ Critical Reminders:

No comma-separated values anywhere.
No paragraph mode for keywords in Section 1.
Ensure each keyword in Section 1 is on its own line in the rendered output.
Minimum 20 total keywords.
Commentary short, tactical, and clean.
Keywords must come from real Etsy product titles.
Products must target $30+ price points.
"""

# --- Display in small, scrollable text area for easy copying ---
st.text_area(
    label="Generated Prompt (for Copying)", 
    value=prompt_template, 
    height=200 # Adjust height in pixels as needed
)

# --- Keyword Research Section ---
st.header("1. Keyword Research")

with st.expander("Generate Etsy Search URLs", expanded=True):
    st.subheader("Enter Keywords")
    # Add input for minimum price
    min_price_filter = st.number_input("Minimum Price (£)", min_value=0, value=25, step=1)
    keywords_input = st.text_area("Enter keywords (one per line):", height=150,
                                  placeholder="ceramic mug\npersonalized necklace\nrustic home decor")

    if keywords_input:
        keywords = [kw.strip() for kw in keywords_input.split('\n') if kw.strip()]
        st.subheader("Clickable Etsy Search Links (Best Sellers > £" + str(min_price_filter) + ")")
        urls_to_open = [] # Store URLs for the button
        for kw in keywords:
            # Pass the min_price_filter to the function
            url = generate_etsy_url(kw, min_price=min_price_filter)
            urls_to_open.append(url) # Add URL to the list
            st.markdown(f"- [{kw}]({url})", unsafe_allow_html=True)

        # Button to open all URLs
        if st.button("🚀 Open All Links in New Tabs"):
            count = 0
            opened_incognito = 0
            os_system = platform.system()
            with st.spinner(f"Attempting to open {len(urls_to_open)} tabs (incognito if possible)..."):
                for url in urls_to_open:
                    incognito_success = False
                    try:
                        cmd_args = []
                        if os_system == "Darwin": # macOS
                            cmd_args = ['open', '-na', 'Google Chrome', '--args', '--incognito', url]
                        elif os_system == "Windows":
                            # Assumes chrome is in PATH. Might need full path on some systems.
                            cmd_args = ['chrome', '--incognito', url]
                        elif os_system == "Linux":
                            # Try google-chrome first, then chromium
                            try:
                                cmd_args = ['google-chrome', '--incognito', url]
                                # Quick check if command likely exists (not foolproof)
                                subprocess.run(['which', 'google-chrome'], check=True, capture_output=True)
                            except (FileNotFoundError, subprocess.CalledProcessError):
                                cmd_args = ['chromium-browser', '--incognito', url] # Fallback for some Linux distros
                        
                        if cmd_args:
                            # Run the command
                            result = subprocess.run(cmd_args, check=False, capture_output=True, text=True) # Use check=False initially
                            if result.returncode == 0:
                                incognito_success = True
                                opened_incognito += 1
                            else:
                                print(f"Incognito command failed: {result.stderr}") # Log error

                    except FileNotFoundError:
                        print(f"Could not find browser command for incognito mode.") # Log error
                    except Exception as e:
                         print(f"Error running incognito command: {e}") # Log other errors

                    # Fallback if incognito failed
                    if not incognito_success:
                        try:
                            webbrowser.open_new_tab(url)
                        except Exception as web_e:
                             st.warning(f"Failed to open {url} in any browser: {web_e}")
                             continue # Skip sleep if opening failed entirely

                    count += 1
                    time.sleep(1) # Wait 1 second between opening tabs

            if opened_incognito > 0:
                st.success(f"Opened {count} tabs. {opened_incognito} attempted in Chrome Incognito (check browser). Others opened in default browser.")
            else:
                 st.success(f"Opened {count} tabs in default browser (could not use Chrome Incognito). ")

    else:
        st.info("Enter some keywords above to generate Etsy search links.")

# --- Opportunity Database Section ---
st.header("2. Opportunity Database")

# --- HTML Paste and Parse Section ---
st.subheader("Parse Product Page HTML")
st.info("Go to the Etsy product page, press Ctrl+A (Select All) then Ctrl+C (Copy), and paste the content below.")

pasted_html = st.text_area("Paste Full HTML Content Here:", height=200, key='pasted_html')

if st.button("Parse Pasted HTML"):
    if st.session_state.pasted_html:
        with st.spinner("Parsing HTML...") :
            try:
                parsed_data = parse_etsy_html_content(st.session_state.pasted_html)

                # --- Update session state (ensure all keys from parsed_data are used) ---
                st.session_state.product_url = parsed_data.get('product_url', st.session_state.product_url) # Keep existing if parse failed
                st.session_state.product_title = parsed_data.get('product_title', st.session_state.product_title)
                st.session_state.price_str = parsed_data.get('price_str', st.session_state.price_str)
                st.session_state.shop_name = parsed_data.get('shop_name', st.session_state.shop_name)
                st.session_state.shop_url = parsed_data.get('shop_url', st.session_state.shop_url)
                st.session_state.processing_time = parsed_data.get('processing_time', st.session_state.processing_time)
                st.session_state.shipping_cost_str = parsed_data.get('shipping_cost_str', st.session_state.shipping_cost_str) # Added

                # Combine notes - append description and reviews
                existing_notes = st.session_state.notes
                # Avoid adding duplicate headers if parsing multiple times
                if "--- Description ---" not in existing_notes and parsed_data.get('description_notes'):
                    existing_notes += "\n\n--- Description ---\n" + parsed_data['description_notes']
                if "--- Review Dates ---" not in existing_notes and parsed_data.get('review_dates_str'):
                    existing_notes += "\n\n--- Review Dates (YYYY-MM-DD) ---\n" + parsed_data['review_dates_str']
                st.session_state.notes = existing_notes.strip()

                st.success("HTML Parsed (using JSON-LD where possible)! Please review fields.")

                # --- Automatically open Everbee link --- 
                if st.session_state.product_title:
                    try:
                        encoded_title = quote_plus(st.session_state.product_title)
                        everbee_url = f"https://app.everbee.io/product-analytics?search_term={encoded_title}"
                        webbrowser.open_new_tab(everbee_url)
                        st.info(f"Opened Everbee Product Analytics search for: '{st.session_state.product_title}' in a new tab.")
                        st.markdown(f"**Everbee Link:** [{everbee_url}]({everbee_url})")
                        st.info("Please copy the HTML from the Everbee page and paste it in the section below.")
                    except Exception as web_e:
                        st.warning(f"Could not automatically open Everbee link: {web_e}")
                        st.markdown(f"**Manual Everbee Link:** [{everbee_url}]({everbee_url})")

            except Exception as e:
                st.error(f"Error parsing HTML: {e}")
                st.exception(e) # Show full traceback for debugging
    else:
        st.warning("Please paste HTML content before parsing.")

# --- Everbee Text Paste and Parse Section --- 
st.subheader("Parse Everbee Page Text")
st.warning("⚠️ Text parsing is fragile and may break if Everbee's page layout changes.")
st.info("Go to the Everbee tab opened previously, press Ctrl+A (Select All) then Ctrl+C (Copy), and paste the **full text content** below.")
pasted_everbee_text = st.text_area("Paste Full Everbee Page Text Content Here:", height=200, key='pasted_everbee_text')

if st.button("Parse Everbee Text"):
    # Check if BOTH Everbee text AND an Etsy shop name are present
    if st.session_state.pasted_everbee_text: # Removed shop_name check as parser finds it
        with st.spinner("Parsing Everbee text (finding highest revenue row)..."):
            try:
                parsed_data = parse_everbee_text_content(st.session_state.pasted_everbee_text)

                # --- Check if parsing succeeded before updating state --- 
                if parsed_data:
                    # --- Update ALL relevant session state fields from parsed_data --- 
                    st.session_state.product_title = parsed_data.get('product_title', st.session_state.product_title)
                    st.session_state.shop_name = parsed_data.get('shop_name', st.session_state.shop_name)
                    st.session_state.price_str = parsed_data.get('price_str_display', str(parsed_data.get('price', ''))) # Use display string or format float
                    st.session_state.est_sales_str = str(parsed_data.get('monthly_sales', ''))
                    st.session_state.est_revenue_str = parsed_data.get('monthly_revenue_str_display', str(parsed_data.get('monthly_revenue', ''))) # Use display string or format float
                    st.session_state.total_sales_str = str(parsed_data.get('total_sales', ''))
                    st.session_state.views_str = str(parsed_data.get('views', ''))
                    st.session_state.favorites_str = str(parsed_data.get('favorites', ''))
                    st.session_state.conversion_rate = parsed_data.get('conversion_rate', st.session_state.conversion_rate)
                    st.session_state.listing_age = parsed_data.get('listing_age', st.session_state.listing_age)
                    st.session_state.shop_age_overall = parsed_data.get('shop_age_overall', st.session_state.shop_age_overall)
                    st.session_state.category = parsed_data.get('category', st.session_state.category)
                    st.session_state.visibility_score = parsed_data.get('visibility_score', st.session_state.visibility_score)
                    st.session_state.review_ratio = parsed_data.get('review_ratio', st.session_state.review_ratio)
                    st.session_state.monthly_reviews_str = str(parsed_data.get('monthly_reviews', ''))
                    st.session_state.listing_type = parsed_data.get('listing_type', st.session_state.listing_type)
                    
                    # Store the parsed tags list in session state
                    st.session_state.tags_list = parsed_data.get('tags_list', [])

                    # --- Append Everbee notes to existing notes --- 
                    existing_notes = st.session_state.notes
                    # Remove previous Everbee sections first (including Tags now)
                    notes_clean = re.sub(r'\n*--- Everbee Tags ---(.*?)(\n*---|$)', '\n\n', existing_notes, flags=re.DOTALL|re.IGNORECASE).strip()
                    notes_clean = re.sub(r'\n*--- Everbee More Details ---(.*?)(\n*---|$)', '\n\n', notes_clean, flags=re.DOTALL|re.IGNORECASE).strip()
                    notes_clean = re.sub(r'\n*--- Everbee Other Data ---(.*?)(\n*---|$)', '\n\n', notes_clean, flags=re.DOTALL|re.IGNORECASE).strip()

                    notes_output = [notes_clean] if notes_clean else []
                    
                    # DO NOT reconstruct Tags section for notes

                    # Reconstruct More Details section directly from parsed_data['more_details_list']
                    if parsed_data.get('more_details_list'):
                        details_section = ["--- Everbee More Details ---"]
                        for detail_dict in parsed_data['more_details_list']:
                            details_section.append(f"- {detail_dict['key']}: {detail_dict['value']}")
                        if len(details_section) > 1:
                            notes_output.append("\n".join(details_section))

                    # Add other Everbee data to notes
                    other_notes_section = ["--- Everbee Other Data ---"]
                    other_data_added = False
                    # Use the values just put into session state
                    if st.session_state.visibility_score: other_notes_section.append(f"Visibility Score: {st.session_state.visibility_score}"); other_data_added = True
                    if st.session_state.review_ratio: other_notes_section.append(f"Review Ratio: {st.session_state.review_ratio}"); other_data_added = True
                    if st.session_state.monthly_reviews_str: other_notes_section.append(f"Monthly Reviews: {st.session_state.monthly_reviews_str}"); other_data_added = True
                    total_shop_sales_val = parsed_data.get('total_shop_sales')
                    if total_shop_sales_val is not None: other_notes_section.append(f"Total Shop Sales: {total_shop_sales_val}"); other_data_added = True

                    if other_data_added:
                        notes_output.append("\n".join(other_notes_section))

                    st.session_state.notes = "\n\n".join(filter(None, notes_output)).strip()

                    st.success("Everbee text parsed! Updated relevant fields and added details to Notes.")
                else:
                    st.warning("Everbee parsing failed. Please check the error message above and the pasted text.")

            except ValueError as ve:
                st.error(f"Error parsing Everbee text: {ve}")
            except Exception as e:
                st.error("An unexpected error occurred during Everbee text parsing.")
                st.exception(e)
    elif not st.session_state.pasted_everbee_text:
        st.warning("Please paste Everbee text content before parsing.")

# --- Manual Entry / Review Form ---
with st.expander("Add/Review Opportunity Details", expanded=True): 
    st.subheader("Product/Shop Data")
    col1, col2 = st.columns(2)

    with col1:
        st.text_input("Product Title", key="product_title")
        st.text_input("Product URL (Etsy - will be auto-cleaned)", key="product_url")
        st.text_input("Shop Name", key="shop_name")
        st.text_input("Shop URL (Etsy - will be auto-cleaned)", key="shop_url")
        st.text_input("Price (e.g., 29.99)", key="price_str")
        st.text_input("Est. Processing + Shipping Time (e.g., 2-8 days)", key="processing_time") 
        st.text_input("Shipping Cost (e.g., 4.99)", key="shipping_cost_str")

    with col2:
        st.text_input("Est. Monthly Revenue (e.g., 10000)", key="est_revenue_str")
        st.text_input("Est. Monthly Sales (e.g., 300)", key="est_sales_str")
        st.text_input("Listing Age (e.g., 17 months)", key="listing_age") # From Everbee
        st.text_input("Shop Age Overall (e.g., 108 Mo.)", key="shop_age_overall") # From Everbee
        st.text_input("Category", key="category") # From Everbee
        st.text_input("Niche Tags (comma-separated)", key="niche_tags")
        st.text_input("Total Sales (Everbee)", key="total_sales_str")
        st.text_input("Total Views (Everbee)", key="views_str")
        st.text_input("Total Favorites (Everbee)", key="favorites_str")
        st.text_input("Conversion Rate (Everbee)", key="conversion_rate")
        st.text_input("Listing Type (Everbee)", key="listing_type")

    st.checkbox("Is Digital Product?", key="is_digital") 

    st.text_area("Potential AliExpress URLs (one per line)", key="aliexpress_urls")
    st.text_area("Notes/Validation (Description/Reviews/Tags/Details added here)", key="notes", height=300) 

    # --- Display Parsed Everbee Tags --- 
    st.subheader("Parsed Everbee Tags")
    if st.session_state.tags_list:
        # Convert list of dicts to DataFrame for display
        tags_df = pd.DataFrame(st.session_state.tags_list)
        # Reorder columns for better readability
        display_columns = ['name', 'volume', 'competition', 'score', 'level']
        tags_df = tags_df[[col for col in display_columns if col in tags_df.columns]] # Ensure columns exist
        st.dataframe(tags_df, use_container_width=True, hide_index=True)
    else:
        st.info("No Everbee tags parsed or available in current session.")

    if st.button("Add/Update Opportunity in Database"):
        # --- Read data from session_state --- 
        product_title = st.session_state.product_title
        product_url = st.session_state.product_url
        shop_name = st.session_state.shop_name
        shop_url = st.session_state.shop_url 
        price_str = st.session_state.price_str
        processing_time = st.session_state.processing_time 
        shipping_cost_str = st.session_state.shipping_cost_str 
        is_digital = st.session_state.is_digital
        est_revenue_str = st.session_state.est_revenue_str
        est_sales_str = st.session_state.est_sales_str
        # shop_age = st.session_state.shop_age # Use shop_age_overall instead
        niche_tags = st.session_state.niche_tags
        aliexpress_urls = st.session_state.aliexpress_urls
        notes = st.session_state.notes

        # New fields from session state
        total_sales_str = st.session_state.total_sales_str
        views_str = st.session_state.views_str
        favorites_str = st.session_state.favorites_str
        conversion_rate = st.session_state.conversion_rate
        listing_age = st.session_state.listing_age
        shop_age_overall = st.session_state.shop_age_overall 
        category = st.session_state.category 
        visibility_score = st.session_state.visibility_score 
        review_ratio = st.session_state.review_ratio 
        monthly_reviews_str = st.session_state.monthly_reviews_str
        listing_type = st.session_state.listing_type 
        
        # Get the tags list from session state
        everbee_tags_list = st.session_state.tags_list

        # --- Data Validation and Type Conversion --- 
        price = None; shipping_cost = None; est_revenue = None; est_sales = None
        total_sales = None; views = None; favorites = None; monthly_reviews = None
        input_valid = True

        if not all([product_title, product_url]): # Only title and URL are strictly required now
            st.warning("Product Title and Product URL are required.")
            input_valid = False
        
        # Validate numeric fields (allow empty)
        def validate_float(val_str, field_name):
            if not val_str: return None, True
            try: return float(val_str.replace(',', '').replace('$', '').replace('£', '').replace('€', '')), True
            except ValueError: st.warning(f"Invalid {field_name} format."); return None, False
        
        def validate_int(val_str, field_name):
            if not val_str: return None, True
            try: return int(val_str.replace(',', '')), True
            except ValueError: st.warning(f"Invalid {field_name} format."); return None, False

        price, price_valid = validate_float(price_str, "Price")
        shipping_cost, sc_valid = validate_float(shipping_cost_str, "Shipping Cost")
        est_revenue, er_valid = validate_float(est_revenue_str, "Est. Monthly Revenue")
        est_sales, es_valid = validate_int(est_sales_str, "Est. Monthly Sales")
        total_sales, ts_valid = validate_int(total_sales_str, "Total Sales")
        views, v_valid = validate_int(views_str, "Total Views")
        favorites, f_valid = validate_int(favorites_str, "Total Favorites")
        monthly_reviews, mr_valid = validate_int(monthly_reviews_str, "Monthly Reviews")
        
        input_valid = input_valid and price_valid and sc_valid and er_valid and es_valid and ts_valid and v_valid and f_valid and mr_valid

        # --- Add to Database if Valid --- 
        if input_valid:
            cleaned_product_url = clean_etsy_url(product_url)
            cleaned_shop_url = clean_etsy_url(shop_url)

            opportunity_data = {
                "product_title": product_title,
                "price": price,
                "product_url": cleaned_product_url,
                "shop_name": shop_name,
                "shop_url": cleaned_shop_url,
                "niche_tags": niche_tags, 
                "est_monthly_revenue": est_revenue,
                "est_monthly_sales": est_sales,
                "shop_age": st.session_state.shop_age, # Keep original shop_age field for now?
                "processing_time": processing_time, 
                "shipping_cost": shipping_cost, 
                "aliexpress_urls": aliexpress_urls.replace('\n', ', '),
                "is_digital": is_digital,
                "notes": notes,
                "total_sales": total_sales,
                "views": views,
                "favorites": favorites,
                "conversion_rate": conversion_rate,
                "listing_age": listing_age,
                "shop_age_overall": shop_age_overall,
                "category": category,
                "visibility_score": visibility_score,
                "review_ratio": review_ratio,
                "monthly_reviews": monthly_reviews,
                "listing_type": listing_type,
                "everbee_tags": everbee_tags_list # Pass the list to the db function
            }
            
            # The db.add_opportunity function now handles JSON conversion
            inserted_id = db.add_opportunity(opportunity_data)
            if inserted_id:
                st.success(f"Successfully added '{product_title}' (ID: {inserted_id}) to the database!")
                init_session_state() # Reset state for next entry
                st.experimental_rerun()
            else:
                st.error("Failed to add opportunity. Does an entry with the same Product URL (after cleaning) already exist?")

# --- Saved Opportunities Display --- 
st.subheader("Saved Opportunities")
opportunities_df = db.get_all_opportunities()

if opportunities_df is None or opportunities_df.empty:
    st.info("No opportunities saved yet or failed to load data. Use the form above after parsing or manual entry!")
else:
    # Basic filtering options (more can be added)
    filter_col1, filter_col2 = st.columns([1, 3])
    with filter_col1:
        filter_term = st.text_input("Filter by Title/Shop/Tags")
    
    filtered_df = opportunities_df
    if filter_term:
        search_mask = (
            filtered_df['product_title'].str.contains(filter_term, case=False, na=False) |
            filtered_df['shop_name'].str.contains(filter_term, case=False, na=False) |
            filtered_df['niche_tags'].str.contains(filter_term, case=False, na=False)
        )
        filtered_df = filtered_df[search_mask]

    # Configure DataFrame display
    st.dataframe(
        filtered_df,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "product_url": st.column_config.LinkColumn("Product URL", display_text="🔗", width="small"),
            "shop_url": st.column_config.LinkColumn("Shop URL", display_text="🔗", width="small"),
            "product_title": st.column_config.TextColumn("Product Title", width="large"),
            "shop_name": st.column_config.TextColumn("Shop Name", width="medium"),
            "price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "shipping_cost": st.column_config.NumberColumn("Shp Cost", format="$%.2f"),
            "processing_time": st.column_config.TextColumn("Prc+Shp Time", width="medium"), 
            "est_monthly_revenue": st.column_config.NumberColumn("Est Rev", format="$%.0f"),
            "est_monthly_sales": st.column_config.NumberColumn("Est Sales"),
            "total_sales": st.column_config.NumberColumn("Total Sales"),
            "views": st.column_config.NumberColumn("Views"),
            "favorites": st.column_config.NumberColumn("Favs"),
            "conversion_rate": st.column_config.TextColumn("Conv Rate"),
            "listing_age": st.column_config.TextColumn("Lst Age"),
            "shop_age_overall": st.column_config.TextColumn("Shp Age"),
            "category": st.column_config.TextColumn("Category", width="medium"),
            "monthly_reviews": st.column_config.NumberColumn("Mo Revs"),
            "listing_type": st.column_config.TextColumn("Type"),
            "niche_tags": st.column_config.TextColumn("Niche Tags", width="medium"),
            "aliexpress_urls": st.column_config.TextColumn("Ali URLs", width="medium"),
             "notes": st.column_config.TextColumn("Notes", width="large"),
            "added_at": st.column_config.DatetimeColumn("Added", format="YYYY-MM-DD HH:mm"),
            "is_digital": st.column_config.CheckboxColumn("Digital?", width="small"),
            # Add others like visibility_score, review_ratio if desired columns
        },
        hide_index=True,
        use_container_width=True
    ) 