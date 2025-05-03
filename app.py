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
    # Ensure it handles potential missing protocol by adding it if needed for parsing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url # Assume https
    try:
        parsed = urlparse(url)
        # Reconstruct URL with only scheme, netloc, path
        cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        # Remove trailing slash if present
        if cleaned.endswith('/'):
             cleaned = cleaned[:-1]
        return cleaned
    except ValueError:
        # Handle potential errors during parsing/unparsing
        print(f"Warning: Could not parse/clean URL: {url}")
        return url # Return original on error

def extract_shop_name_from_url(url):
    """Extracts the shop name from a cleaned Etsy shop URL."""
    if not url or not isinstance(url, str):
        return None
    # Regex to find /shop/ followed by the shop name characters
    match = re.search(r'/shop/([A-Za-z0-9_-]+)', url)
    if match:
        return match.group(1)
    return None

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
    print("\\n--- DEBUG Everbee: Starting parse_everbee_text_content (Generalized Approach) ---") # DEBUG Start

    # --- Normalize & Split Lines (Keep Robust Logic) ---
    # ... (existing normalization and splitting logic remains) ...
    try:
        normalized_text = page_text.replace('\\\\n', '\\n') # Replace literal \\n
        lines_unfiltered = normalized_text.splitlines()
        lines = [line.strip() for line in lines_unfiltered if line.strip()]
        num_lines = len(lines)
        print(f"DEBUG Everbee: Processed {num_lines} non-empty lines.")
        if not lines:
            st.error("Error parsing Everbee text: No content found after splitting lines.")
            print("ERROR Everbee: No non-empty lines found after splitting.")
            return None
    except Exception as e:
        st.error(f"Error during robust line splitting: {e}")
        print(f"ERROR Everbee: Exception during robust line splitting: {e}")
        return None
    # --- END Normalize & Split ---

    # --- Define Helper Functions (Keep) ---
    # ... (safe_int, safe_float functions remain) ...
    def safe_int(val_str): 
        if not val_str: return None
        try: 
            cleaned_str = str(val_str).replace(',', '')
            return int(cleaned_str)
        except (ValueError, TypeError): return None
    def safe_float(val_str, field_name="value"):
        if not val_str: return None
        try:
            cleaned = re.sub(r'[\\$\\£€,]', '', str(val_str)).strip()
            return float(cleaned)
        except (ValueError, TypeError): return None

    # --- Step 1: Flexible Boundary Detection (Attempt, but don't strictly enforce) ---
    print("\\nDEBUG Everbee Boundaries: Attempting flexible boundary detection...")
    table_start_index = -1
    table_end_index = num_lines # Default to parsing everything if end not found
    header_line_index = -1
    start_keywords = ["Customize button in Toolbar", "Filter button in Toolbar", "Export button in Toolbar"]
    header_keyword = "Product"
    end_keywords_regex = [r"^Showing: \\d+ of \\d+$", r"^Listing Details$", r"^Tags$", r"^Related Searches$", r"^Keyword Score$", r"^Trends$"] # Combined end markers

    # Try finding button markers first
    button_marker_index = -1
    for i, line in enumerate(lines[:60]): # Limit initial search
        if any(kw in line for kw in start_keywords):
            button_marker_index = i
            print(f"DEBUG Everbee Boundaries: Found button marker near line {i}: '{line}'")
            # Look for "Product" header shortly after
            for j in range(i + 1, min(i + 10, num_lines)):
                if lines[j].strip() == header_keyword:
                    header_line_index = j
                    table_start_index = header_line_index + 1 # Start after header
                    print(f"DEBUG Everbee Boundaries: Found '{header_keyword}' header at index {header_line_index}. Tentative start index: {table_start_index}")
                    break
            if header_line_index != -1: break

    # Fallback: Look for "Product" header anywhere early if button method failed
    if table_start_index == -1:
        for i in range(min(60, num_lines)): # Broader search
            if lines[i].strip() == header_keyword:
                header_line_index = i
                table_start_index = header_line_index + 1
                print(f"DEBUG Everbee Boundaries: Found '{header_keyword}' header via fallback at index {i}. Tentative start index: {table_start_index}")
                break

    if table_start_index == -1:
         print("WARNING Everbee Boundaries: Could not reliably determine table start via 'Product' header. Parsing will attempt labels from line 0.")
         table_start_index = 0 # Default to start if no header found

    # Find the first occurrence of any end marker *after* the potential start
    for i in range(table_start_index, num_lines):
        if any(re.match(kw, lines[i], re.IGNORECASE) for kw in end_keywords_regex):
            table_end_index = i
            print(f"DEBUG Everbee Boundaries: Found potential end marker '{lines[i]}' at line {i}. Tentative table end index.")
            break

    print(f"DEBUG Everbee Boundaries: Tentative parsing range for labels: {table_start_index} to {table_end_index-1}")
    # --- End Step 1 ---

    # --- Step 2: Heuristics for Title/Shop Name (Cautious) ---
    print("\\nDEBUG Everbee Heuristic: Attempting Title/Shop heuristic...")
    heuristic_start_line = table_start_index # Start search from where table might begin
    product_title_heuristic = None
    shop_name_heuristic = None
    lines_processed_heuristic = 0

    # Look for first non-numeric/non-common-label line as title
    if heuristic_start_line < num_lines:
        line1 = lines[heuristic_start_line]
        # Simple check: not starting like a price/number and has letters
        if not re.match(r'^[\\$\\£€\\d]', line1) and re.search(r'[a-zA-Z]', line1) and line1 != "Product/Shop Image":
             product_title_heuristic = line1
             print(f"DEBUG Everbee Heuristic: Tentative Product Title: '{product_title_heuristic}' (from line {heuristic_start_line})")
             lines_processed_heuristic += 1
             
             # Look for shop name on the *next* line
             next_line_idx = heuristic_start_line + 1
             if next_line_idx < num_lines:
                 line2 = lines[next_line_idx]
                 # Check if it's NOT a price/number/age/etc.
                 if not re.match(r'^[\\$\\£€\\d]', line2) and not re.match(r'^\d+\s+(Mo\.?|months?)$', line2, re.IGNORECASE) and re.search(r'[a-zA-Z]', line2):
                     shop_name_heuristic = line2
                     print(f"DEBUG Everbee Heuristic: Tentative Shop Name: '{shop_name_heuristic}' (from line {next_line_idx})")
                     lines_processed_heuristic += 1
                 else:
                     print(f"DEBUG Everbee Heuristic: Line after title ('{line2}') looks like data, not shop name.")
             else:
                 print("DEBUG Everbee Heuristic: No line found after potential title.")
        else:
            print(f"DEBUG Everbee Heuristic: First line ('{line1}') doesn't look like a title.")
    else:
        print("DEBUG Everbee Heuristic: No lines available for heuristic.")

    # Assign heuristic values ONLY if they aren't found later by labels
    if product_title_heuristic: parsed_data['product_title'] = product_title_heuristic
    if shop_name_heuristic: parsed_data['shop_name'] = shop_name_heuristic
    # --- End Step 2 ---

    # --- Step 3: Comprehensive Label-Value Matching ---
    print("\\nDEBUG Everbee Label Match: Starting comprehensive label matching...")
    # Combined map for table data AND potential "Listing Details" data
    # Key: Label text (lowercase for matching)
    # Value: (target_key_in_parsed_data, conversion_function or None, optional_validation_regex)
    label_map = {
        "price": ("price_str", None, r'^[$£€][\d,.]+$'), # Corrected Price regex too
        "shop": ("shop_name", None, None), # Explicit shop label
        "mo. sales": ("monthly_sales", safe_int, r'^[\d,]+$'),
        "mo. revenue": ("monthly_revenue_str_display", None, r'^[$£€][\d,.]+$'), 
        "total sales": ("total_sales", safe_int, r'^[\d,]+$'),
        "listing age": ("listing_age", None, r'^\d+\s+(?:Mo\.?|months?)$'), # Keep raw string
        "reviews": ("reviews", safe_int, r'^[\d,]+$'),
        "views": ("views", safe_int, r'^[\d,]+$'),
        "favorites": ("favorites", safe_int, r'^[\d,]+$'),
        "mo. reviews": ("monthly_reviews", safe_int, r'^[\d,]+$'), # Often under "Listing Details"
        "conversion rate": ("conversion_rate", None, r'^[\d.]+%?$'), # Store raw, strip % later if needed
        "category": ("category", None, r'.+'), # Any non-empty
        "visibility score": ("visibility_score", None, r'^\d+%?$'), # Store raw, strip % later if needed
        "review ratio": ("review_ratio", None, r'^[\d.]+%?$'), # Often under "Listing Details"
        # --- Screenshot specific labels ---
        "shop age": ("shop_age_overall", None, r'^\d+\s+(?:Mo\.?|months?)$'), # Get from table if possible
        "total shop sales": ("total_shop_sales", safe_int, r'^[\d,]+$'),
        "listing type": ("listing_type", None, r'^(Physical|Digital)$'),
        "avg. reviews": ("monthly_reviews", safe_int, r'^[\d,]+$') # Map screenshot "Avg. Reviews" to monthly_reviews
    }

    # Start loop *after* heuristic lines, but respect potential boundaries
    # Use table_start_index as a guide but allow parsing beyond table_end_index if needed
    parse_loop_start_index = table_start_index + lines_processed_heuristic
    print(f"DEBUG Everbee Label Match: Starting loop from index {parse_loop_start_index}...")

    i = parse_loop_start_index
    while i < num_lines:
        current_line = lines[i]
        current_line_lower = current_line.lower() # Match labels case-insensitively
        matched_label_info = None
        label_text_matched = None

        # Skip known noise explicitly first
        if current_line == "Dots Svg":
            print(f"DEBUG Everbee Label Match: Skipping noise line 'Dots Svg' at index {i}")
            i += 1
            continue

        # Check if the current line IS a label
        for label, info in label_map.items():
            if current_line_lower == label:
                matched_label_info = info
                label_text_matched = label
                break

        if matched_label_info:
            target_key, conversion_func, validation_regex = matched_label_info
            print(f"DEBUG Everbee Label Match: Found label '{label_text_matched}' at line {i}. Target key: '{target_key}'")

            # Look ahead for the value line(s)
            value_line_index = i + 1
            raw_value = None
            lines_to_skip = 1 # Assume we skip label + value = 2 lines total

            if value_line_index < num_lines:
                raw_value = lines[value_line_index]
                print(f"DEBUG Everbee Label Match:   -> Potential value: '{raw_value}' (from line {value_line_index})")

                # Validate value if regex provided
                if validation_regex and not re.match(validation_regex, raw_value, re.IGNORECASE):
                    print(f"WARNING Everbee Label Match:   -> Value '{raw_value}' failed validation regex: {validation_regex}. Skipping assignment.")
                    raw_value = None # Invalidate value
                    lines_to_skip = 1 # Only skip the label line if value is bad/missing

                # Assign if value is valid
                if raw_value is not None:
                    try:
                        # Clean specific values before conversion/assignment
                        if target_key == 'visibility_score' and isinstance(raw_value, str): raw_value = raw_value.replace('%', '')
                        if target_key == 'conversion_rate' and isinstance(raw_value, str): raw_value = raw_value.replace('%', '')
                        if target_key == 'review_ratio' and isinstance(raw_value, str): raw_value = raw_value.replace('%', '')

                        final_value = conversion_func(raw_value) if conversion_func else raw_value

                        # Logic to handle overwriting heuristics vs existing labeled data
                        should_assign = True
                        if target_key in parsed_data:
                             # If target is shop_name and we found it via heuristic, allow overwrite
                             if target_key == 'shop_name' and parsed_data.get('shop_name') == shop_name_heuristic and shop_name_heuristic != final_value:
                                 print(f"INFO Everbee Label Assign: Overwriting heuristic shop '{shop_name_heuristic}' with labeled shop '{final_value}'")
                             # If target is title and we found it via heuristic, allow overwrite
                             elif target_key == 'product_title' and parsed_data.get('product_title') == product_title_heuristic and product_title_heuristic != final_value:
                                 print(f"INFO Everbee Label Assign: Overwriting heuristic title '{product_title_heuristic}' with labeled title '{final_value}'")
                             # Otherwise, generally avoid overwriting data already found via labels
                             elif (target_key == 'shop_name' and parsed_data.get('shop_name') != shop_name_heuristic) or \
                                  (target_key == 'product_title' and parsed_data.get('product_title') != product_title_heuristic):
                                 print(f"DEBUG Everbee Label Assign: Key '{target_key}' already exists with labeled value '{parsed_data[target_key]}'. Skipping assignment of '{final_value}'.")
                                 should_assign = False
                             # Allow monthly_reviews overwrite (heuristic vs avg. reviews vs mo. reviews)
                             elif target_key == 'monthly_reviews':
                                  print(f"DEBUG Everbee Label Assign: Allowing overwrite for 'monthly_reviews'. Old: {parsed_data.get(target_key)}, New: {final_value}")
                             else: # For other keys, avoid simple overwrite if already set
                                  print(f"DEBUG Everbee Label Assign: Key '{target_key}' already set to '{parsed_data[target_key]}'. Skipping assignment of '{final_value}'.")
                                  should_assign = False

                        if should_assign:
                            parsed_data[target_key] = final_value
                            print(f"DEBUG Everbee Label Assign: Assigned '{target_key}' = {repr(final_value)}")

                    except Exception as assign_e:
                        print(f" !!!!! ERROR Everbee Label Assign: Failed converting/assigning label '{label_text_matched}' (raw: {repr(raw_value)}) to '{target_key}': {assign_e}")
                        # Don't assign None, just skip if conversion fails
                    
                    # We processed label and value
                    lines_to_skip = 2

            else: # No line found after label
                print(f"WARNING Everbee Label Match: Found label '{label_text_matched}' but no value line followed.")
                lines_to_skip = 1 # Skip only the label

            i += lines_to_skip # Advance past label (and value if processed)
            continue # Go to next iteration

        # If line is not a label, just advance
        i += 1
    # --- End Step 3 ---

    # --- Remove obsolete parsing sections (Old Table Chunking, Listing Details Separate Parse) ---
    # (Code for fixed chunk parsing and separate details parsing is removed)

    # --- Step 4: Parse Dedicated Sections (Trends, Tags, More Details) ---
    # These sections have clearer boundaries and specific logic

    # --- Trends (Keep Logic, ensure runs after main label parsing) ---
    print("\\nDEBUG Everbee Trends: Searching for 'Last 30 Days Sales'...")
    # ... (Existing robust Trends search logic remains) ...
    trends_search_start_index = -1 
    trends_search_end_index = num_lines 
    for j, line in enumerate(lines): # Renamed loop variable
        if line.strip().lower() == 'trends':
             trends_search_start_index = j + 1 
             print(f"DEBUG Everbee Trends: Found 'Trends' header at line {j}.")
             break
    if trends_search_start_index != -1:
        for j in range(trends_search_start_index, num_lines): # Renamed loop variable
            line_lower = lines[j].strip().lower()
            if line_lower in ['tags', 'more details', 'related searches', 'listing details']: # Added listing details as end marker too
                trends_search_end_index = j
                print(f"DEBUG Everbee Trends: Found end marker '{line_lower}' at line {j}.")
                break
        print(f"DEBUG Everbee Trends: Search range lines {trends_search_start_index}-{trends_search_end_index-1}")
        last_30_sales_value_str = None
        # ... (Inner logic to find 'sales', number, 'revenue' sequence remains the same) ...
        for k in range(trends_search_start_index, trends_search_end_index): # Renamed loop variable
            line_lower = lines[k].strip().lower()
            if line_lower == 'sales':
                 potential_sales_line_idx = -1; potential_sales_val = None; revenue_found_nearby = False
                 for l in range(k + 1, min(k + 4, trends_search_end_index)): # Renamed loop variable
                     line_to_check = lines[l].strip()
                     sales_val_match = re.match(r'^([\d,]+)$', line_to_check) # Accept commas too
                     if sales_val_match: potential_sales_val = sales_val_match.group(1); potential_sales_line_idx = l; break
                     elif line_to_check.lower() == 'revenue': potential_sales_val = None; break
                 if potential_sales_val is not None and potential_sales_line_idx != -1:
                     for m in range(potential_sales_line_idx + 1, min(potential_sales_line_idx + 4, trends_search_end_index)): # Renamed loop variable
                         if lines[m].strip().lower() == 'revenue': revenue_found_nearby = True; break
                 if potential_sales_val is not None and revenue_found_nearby:
                     last_30_sales_value_str = potential_sales_val
                     print(f"DEBUG Everbee Trends: ===> CONFIRMED Last 30 Days Sales value: {last_30_sales_value_str} (near line {k}) <===") 
                     break # Stop searching trends section
        if last_30_sales_value_str:
             parsed_data['last_30_days_sales'] = last_30_sales_value_str # Store as string
             print(f"DEBUG Everbee Assign Trends: Assigned Last 30 Days Sales = {repr(parsed_data.get('last_30_days_sales'))}")
        else: print("DEBUG Everbee Trends: Did not find confirmed 'Last 30 Days Sales' pattern.")
    else: print("DEBUG Everbee Trends: 'Trends' header not found.")
    # --- End Trends ---

    # --- Tags (Keep Logic) ---
    print("\nDEBUG Everbee Tags: Starting Tags section parsing...")
    # ... (Existing robust Tags parsing logic remains the same) ...
    # Including finding start ('Keyword Score' or 'Tags') and end ('More Details') markers
    tags_list = []

    try:
        block_start_index = -1
        block_end_index = num_lines
        details_marker_index = -1

        # Find Tags start/end
        for j, line in enumerate(lines):  # Renamed loop variable
            if re.match(r'^Keyword Score$', line, re.IGNORECASE):
                block_start_index = j + 1
                break

        if block_start_index == -1:
            for j, line in enumerate(lines):  # Renamed loop variable
                if re.match(r'^Tags$', line, re.IGNORECASE):
                    block_start_index = j + 1
                    break

        if block_start_index != -1:
            print(f"DEBUG Everbee Tags: Found start marker around line {block_start_index-1}")

            # Skip possible header rows
            while (
                block_start_index < num_lines
                and re.match(r'^(Volume|Competition|Keyword Score)\s*$', lines[block_start_index], re.IGNORECASE)
            ):
                block_start_index += 1

            # Find the end marker
            for k in range(block_start_index, num_lines):  # Renamed loop variable
                if re.match(r'^\s*More Details\s*$', lines[k], re.IGNORECASE):
                    block_end_index = k
                    details_marker_index = k
                    print(f"DEBUG Everbee Tags: Found end marker at line {k}")
                    break

            if block_start_index < block_end_index:
                # ... (Inner loop processing tag_block_lines remains the same) ...
                tag_block_lines = lines[block_start_index:block_end_index]
                num_tag_lines = len(tag_block_lines)
                print(f"DEBUG Everbee Tags: Processing {num_tag_lines} lines in tag block.")

                k = 0  # Renamed loop variable
                while k < num_tag_lines:
                    current_tag = {}
                    lines_consumed = 0

                    try:
                        # 1. Tag Name
                        if k < num_tag_lines:
                            line1 = tag_block_lines[k]
                            if (
                                line1
                                and re.search(r'[a-zA-Z]', line1)
                                and not re.match(r'^[\d,\.\s%]+$', line1)
                                and not re.match(r'^(High|Medium|Low)$', line1, re.IGNORECASE)
                            ):
                                current_tag['name'] = line1
                                lines_consumed += 1
                            else:
                                k += 1
                                continue  # Skip to next line if name pattern fails
                        else:
                            break  # End of block

                        # 2. Volume
                        vol_idx = k + lines_consumed
                        if vol_idx < num_tag_lines:
                            line2 = tag_block_lines[vol_idx]
                            vol_match = re.match(r'^([\d,]+)$', line2)
                            if vol_match:
                                current_tag['volume'] = vol_match.group(1)
                                lines_consumed += 1
                            else:
                                k += 1
                                continue  # Skip to next line if volume pattern fails
                        else:
                            break  # End of block

                        # 3. Competition
                        comp_idx = k + lines_consumed
                        if comp_idx < num_tag_lines:
                            line3 = tag_block_lines[comp_idx]
                            comp_match = re.match(r'^([\d,]+)$', line3)
                            if comp_match:
                                current_tag['competition'] = comp_match.group(1)
                                lines_consumed += 1
                            else:
                                k += 1
                                continue  # Skip to next line if competition pattern fails
                        else:
                            break  # End of block

                        # 4. Level (Optional)
                        level_idx = k + lines_consumed
                        current_tag['level'] = 'N/A'  # Default
                        if level_idx < num_tag_lines:
                            line4 = tag_block_lines[level_idx]
                            level_match = re.match(r'^(High|Medium|Low)$', line4, re.IGNORECASE)
                            if level_match:
                                current_tag['level'] = level_match.group(1)
                                lines_consumed += 1
                        # Level is optional

                        # 5. Score
                        score_idx = k + lines_consumed
                        if score_idx < num_tag_lines:
                            line5 = tag_block_lines[score_idx]
                            # Correct the regex: Remove trailing $'
                            score_match = re.match(r'^([\d,.]+)$', line5)
                            if score_match:
                                current_tag['score'] = score_match.group(1)
                                lines_consumed += 1
                                tags_list.append(current_tag)
                                k += lines_consumed # Advance past the full tag entry
                                continue # Move to the next potential tag entry
                            else:
                                # Score missing – assume misalignment, advance one line
                                k += 1
                                continue
                        else:
                            break  # End of block

                    except Exception as tag_loop_e:
                        print(f"ERROR Everbee Tags Loop: {tag_loop_e} at index {k}")
                        k += 1
                        continue
                # End while tag lines

                if tags_list:
                    parsed_data['tags_list'] = tags_list
                    print(f"DEBUG Everbee Tags: Assigned {len(tags_list)} tags.")
            else:
                print("DEBUG Everbee Tags: Block start not before end.")
        else:
            print("DEBUG Everbee Tags: Block start marker not found.")
    except Exception as e:
        print(f"ERROR Everbee Tags: EXCEPTION during parsing: {e}")
    # --- End Tags ---

    # --- More Details (Keep Logic, ensure respects listing_type if already set) ---
    print("\\nDEBUG Everbee More Details: Starting More Details section parsing...")
    # ... (Existing More Details parsing logic remains the same) ...
    # Uses details_marker_index from Tags section if found, otherwise searches again
    details_start_index = details_marker_index + 1 if details_marker_index != -1 and details_marker_index + 1 < num_lines else -1
    if details_start_index == -1: # Fallback search
        for j, line in enumerate(lines): # Renamed loop variable
             if re.match(r'^\s*More Details\s*$', line, re.IGNORECASE): details_start_index = j + 1; print(f"DEBUG Everbee Details: Found header via fallback at line {j}"); break
    if details_start_index != -1 and details_start_index < num_lines:
        details_list = []
        known_keys = ["When Made", "Listing Type", "Customizable", "Craft Supply", "Personalized", "Auto Renew", "Has variations", "Placements of Listing Shops", "Title character count", "# of tags", "Who Made"]
        key_regex_map = {key: re.compile(r'^\s*' + re.escape(key) + r'\s*$', re.IGNORECASE) for key in known_keys}
        current_key = None; current_value_lines = []
        print(f"DEBUG Everbee Details: Processing details from line {details_start_index}...")
        for j in range(details_start_index, num_lines): # Renamed loop variable
            line = lines[j].strip()
            if not line:
                continue
            # Ensure initialization happens at the start of each outer loop iteration
            is_known_key = False 
            matched_key = None
            for key, key_regex in key_regex_map.items():
                if key_regex.match(line):
                    is_known_key = True
                    matched_key = key
                    break
            if is_known_key:
                if current_key and current_value_lines:
                    value = ' '.join(current_value_lines).strip()
                    if current_key == 'Who Made' and isinstance(value, str): value = re.sub(r'\s+\d+$', '', value).strip()
                    details_list.append({'key': current_key, 'value': value or 'Unknown'})
                    # Assign listing type ONLY IF NOT ALREADY FOUND
                    if current_key == 'Listing Type' and 'listing_type' not in parsed_data:
                         parsed_data['listing_type'] = value or 'Unknown'
                         print(f"DEBUG Everbee Details Assign: Assigned listing_type='{parsed_data['listing_type']}' from Details section.")
                current_key = matched_key; current_value_lines = []
            elif current_key: current_value_lines.append(line)
        if current_key and current_value_lines: # Process last key
            value = ' '.join(current_value_lines).strip()
            if current_key == 'Who Made' and isinstance(value, str): value = re.sub(r'\s+\d+$', '', value).strip()
            details_list.append({'key': current_key, 'value': value or 'Unknown'})
            if current_key == 'Listing Type' and 'listing_type' not in parsed_data:
                 parsed_data['listing_type'] = value or 'Unknown'
                 print(f"DEBUG Everbee Details Assign: Assigned listing_type='{parsed_data['listing_type']}' from Details section (final key).")
        if details_list:
             notes.append("\\n--- Everbee More Details ---")
             for detail_dict in details_list: notes.append(f"- {detail_dict['key']}: {detail_dict['value']}")
             print(f"DEBUG Everbee Details: Added {len(details_list)} items to notes.")
        else: print("DEBUG Everbee Details: No details parsed.")
    else: print("DEBUG Everbee Details: 'More Details' header not found.")
    # --- End More Details ---

    # --- Step 5: Final Fallback Pass for Shop Age Overall ---
    # Only run if 'shop_age_overall' wasn't found via label matching
    if 'shop_age_overall' not in parsed_data:
        print("\\nDEBUG Everbee Final Pass: Searching for Shop Age Overall fallback...")
        listing_age_val = parsed_data.get('listing_age') # Get listing age if found
        found_distinct_age = None
        age_pattern = r'^(\d+\s+(?:Mo\.?|months?))$' # Regex for age
        for i, line in enumerate(lines):
            age_match = re.match(age_pattern, line.strip(), re.IGNORECASE)
            if age_match:
                potential_shop_age = age_match.group(1)
                # Normalize for comparison (e.g., "12 months" vs "12 Mo.")
                norm_potential = potential_shop_age.lower().replace('months', 'mo').replace('.', '')
                norm_listing = str(listing_age_val).lower().replace('months', 'mo').replace('.', '') if listing_age_val else None
                
                if norm_listing is None or norm_potential != norm_listing:
                    found_distinct_age = potential_shop_age # Store the first distinct age found
                    print(f"DEBUG Everbee Final Pass: Found distinct Shop Age Overall '{found_distinct_age}' at line {i}")
                    break # Stop after finding the first distinct one

        if found_distinct_age:
             parsed_data['shop_age_overall'] = found_distinct_age
        else:
             print("DEBUG Everbee Final Pass: Shop Age Overall not found or matched listing age.")
    else:
        print("\\nDEBUG Everbee Final Pass: Shop Age Overall already found via label matching. Skipping fallback.")
    # --- End Step 5 ---

    # Final check and return
    parsed_data['notes'] = "\\n".join(notes)
    print(f"\\nDEBUG Everbee: Final parsed_data keys: {list(parsed_data.keys())}")
    print("--- DEBUG Everbee: Finished parse_everbee_text_content (Generalized Approach) ---")
    return parsed_data

# --- ERANK Analysis Helper Functions ---
def parse_erank_text_content(erank_text):
    """
    Parses pasted text content from the ERANK Keyword Tool page using a procedural,
    chunk-based approach.
    Extracts Keyword, Avg. Searches, Avg. Clicks, Avg. CTR, Etsy Competition,
    Google Searches, the Seed Keyword, and the Country Code.
    Returns: tuple (seed_keyword, country_code, keywords_data_list) or (None, None, []) on failure.
    """
    lines = [line.strip() for line in erank_text.strip().splitlines()] # Ensure lines are stripped
    keywords_data = []
    extracted_seed_keyword = None
    extracted_country_code = None # <-- Initialize country code
    seed_keyword_line_index = -1
    data_start_index = -1
    data_end_index = len(lines)

    # --- 1. Find Seed Keyword (Simplified Extraction) ---
    seed_line_prefix = "Keywords related to" 
    # print(f"DEBUG ERANK: Checking for prefix: '{seed_line_prefix}'") # Keep commented for now
    # print("DEBUG ERANK: First 50 lines after split/strip:") # Keep commented for now
    # for idx, l in enumerate(lines[:50]):
    #     print(f"  Line {idx}: '{l}'")
        
    # ENSURE Corrected quote char lists
    quote_chars_open = ["\"", "'", ""] # Restore opening smart quote
    quote_chars_close = ["\"", "'", ""] # Restore closing smart quote
        
    for i, line in enumerate(lines):
        # print(f"DEBUG ERANK SEED CHECK: Line {i}: '{line}'") # Keep this commented
        stripped_line = line.strip()
        # Looser check: look for prefix, then find quotes shortly after
        if stripped_line.startswith("Keywords related to"): 
            print(f"DEBUG ERANK SEED FOUND PREFIX: Matched prefix on line {i}. Content: '{line}'")
            try:
                # Find where the prefix actually ends
                prefix_actual_end = stripped_line.find(seed_line_prefix) + len(seed_line_prefix)
                
                # Now search for the first opening quote *after* the prefix end
                first_quote_index = -1
                opening_quote_char = None
                for quote in quote_chars_open:
                    # Start search from prefix_actual_end
                    idx = stripped_line.find(quote, prefix_actual_end)
                    if idx != -1:
                        # Check if this is the first quote found *or* closer than previous finds
                        if first_quote_index == -1 or idx < first_quote_index:
                            first_quote_index = idx
                            opening_quote_char = quote
                
                # If no opening quote found shortly after prefix, skip this line
                if first_quote_index == -1 or first_quote_index > prefix_actual_end + 5: # Allow a few spaces
                    print(f"DEBUG ERANK SEED: Found prefix but no opening quote nearby on line {i}.")
                    continue # Move to next line

                # Find the first corresponding closing quote *after* the opening one
                closing_quote_index = -1
                if opening_quote_char:
                    matching_close_quote = None
                    if opening_quote_char == '"':
                        matching_close_quote = '"'
                    elif opening_quote_char == "'":
                        matching_close_quote = "'"
                    elif opening_quote_char == '': # Restore check for opening smart quote
                        matching_close_quote = '"' # Restore map to closing smart quote
                    
                    if matching_close_quote:
                         idx_match = stripped_line.find(matching_close_quote, first_quote_index + 1)
                         if idx_match != -1:
                             closing_quote_index = idx_match
                    
                    if closing_quote_index == -1: # Fallback if matching type not found
                        for quote in quote_chars_close:
                            idx = stripped_line.find(quote, first_quote_index + 1)
                            if idx != -1:
                                if closing_quote_index == -1 or idx < closing_quote_index:
                                    closing_quote_index = idx

                if closing_quote_index != -1:
                    keyword_part = stripped_line[first_quote_index + 1 : closing_quote_index].strip()
                    if keyword_part:
                        extracted_seed_keyword = keyword_part
                        seed_keyword_line_index = i
                        print(f"DEBUG ERANK: Found seed keyword '{extracted_seed_keyword}' at line {i} (Looser Prefix Check)")
                        break 
                else:
                    print(f"DEBUG ERANK: Found prefix and opening quote '{opening_quote_char}' but no closing quote on line {i}.")
            except Exception as e:
                print(f"DEBUG ERANK: Error extracting seed keyword from line '{line}': {e}")

    if seed_keyword_line_index == -1:
        print("DEBUG ERANK: Seed keyword line ('Keywords related to...') not found.")

    # --- 1b. Find Country Code (Before data processing) ---
    country_line_prefix = "Search Trends ("
    for i, line in enumerate(lines):
         # Limit search range reasonably (e.g., before expected data start)
         # Increase limit slightly as it might appear after seed keyword line
         if seed_keyword_line_index != -1 and i > seed_keyword_line_index + 30: # Heuristic limit
              break
         # If seed keyword wasn't found, search more broadly
         if seed_keyword_line_index == -1 and i > 50: # Broader limit if no seed found
              break
              
         if line.strip().startswith(country_line_prefix):
             match = re.search(r'\((.*?)\)', line) # Find text within parentheses
             if match and match.group(1):
                 extracted_country_code = match.group(1).strip()
                 print(f"DEBUG ERANK: Found country code '{extracted_country_code}' at line {i}")
                 break # Found it, stop searching for country
                 
    if not extracted_country_code:
         print("DEBUG ERANK: Country code ('Search Trends (XXX)') not found. Defaulting to Unknown.")
         extracted_country_code = "Unknown"

    # --- 2. Find Data Start Marker ---
    exclude_marker = "EXCLUDE KEYWORDS"
    exclude_marker_found = False
    # Start search from beginning or after seed keyword if found
    start_search_idx = seed_keyword_line_index + 1 if seed_keyword_line_index != -1 else 0
    for i in range(start_search_idx, len(lines) - 1): # Check up to second-to-last line
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
                 # Return seed/country if found, but empty list as no data possible
                 return extracted_seed_keyword, extracted_country_code, []

    if data_start_index == -1:
        if exclude_marker_found:
             print(f"DEBUG ERANK: Found '{exclude_marker}' but failed to set data start index.")
        else:
             print(f"DEBUG ERANK: Data start marker ('{exclude_marker}') not found after seed keyword line.")
        # Return seed/country if found, but empty list as no data found
        return extracted_seed_keyword, extracted_country_code, []

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
    # ... (chunk processing loop remains the same) ...
    current_index = data_start_index
    while current_index < data_end_index:
        # Check if enough lines remain for a potential 9-line chunk
        if current_index + 8 >= data_end_index:
            break

        # --- Define the 9 lines for the current chunk ---
        line1_kw = lines[current_index]       # Keyword
        line2_date = lines[current_index + 1]   # Date (Not used anymore but part of structure)
        line3_trend = lines[current_index + 2]  # Trend
        line4_counts = lines[current_index + 3] # Char Count 	 Tag Occurrences
        line5_search = lines[current_index + 4] # Avg Searches
        line6_clicks = lines[current_index + 5] # Avg Clicks
        line7_ctr = lines[current_index + 6]    # Avg CTR
        line8_comp = lines[current_index + 7]   # Etsy Competition
        line9_goog = lines[current_index + 8]   # Google Searches

        # --- Basic Validation of Chunk Structure ---
        is_likely_keyword = bool(re.search(r'[a-zA-Z]', line1_kw)) and not line1_kw.replace(' ', '').isdigit()
        has_tab_in_line4 = '\t' in line4_counts # Check for literal tab character
        # More robust check for line 4 format like '18	26'
        counts_match = re.match(r'^\d+\s+\d+$', line4_counts)
        is_likely_google = bool(re.match(r'^([\d,]+|N/A|Unknown)$', line9_goog, re.IGNORECASE))

        if is_likely_keyword and counts_match and is_likely_google:
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
                current_index += 9 # Move to the next potential chunk
            except Exception as e:
                print(f"DEBUG ERANK Chunk: ERROR extracting data from valid-looking chunk at index {current_index}: {e}")
                current_index += 1
                while current_index < data_end_index and not lines[current_index]: current_index += 1
        else:
            # Debugging invalid structure detection
            # print(f"DEBUG ERANK Chunk: Invalid structure detected at index {current_index}. Skipping line.")
            # print(f"  L1 Kw: '{line1_kw}' (Likely:{is_likely_keyword})")
            # print(f"  L4 Cts: '{line4_counts}' (Tab:{has_tab_in_line4}, Match:{bool(counts_match)})")
            # print(f"  L9 Goog: '{line9_goog}' (Likely:{is_likely_google})")
            current_index += 1
            while current_index < data_end_index and not lines[current_index]: current_index += 1


    print(f"\nERANK Summary: Parsing loop finished. Extracted {len(keywords_data)} keyword entries.")
    return extracted_seed_keyword, extracted_country_code, keywords_data # <-- Return country code

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
tab1, tab2, tab3 = st.tabs(["Opportunity Tracker", "ERANK Keyword Analysis", "High Performing Etsy Shops"])

# =========================== #
# === Opportunity Tracker Tab === #
# =========================== #
with tab1:
    # --- Check if form should be cleared --- 
    if st.session_state.get('clear_form', False):
        form_keys_to_clear = [
            'opp_form_product_title', 'opp_form_product_url', 'opp_form_shop_name',
            'opp_form_shop_url', 'opp_form_price_str', 'opp_form_processing_time',
            'opp_form_shipping_cost_str', 'opp_form_est_revenue_str', 'opp_form_est_sales_str',
            'opp_form_last_30_days_sales_str', 'opp_form_last_30_days_revenue_str',
            'opp_form_listing_age', 'opp_form_shop_age_overall', 'opp_form_category',
            'opp_form_niche_tags', 'opp_form_total_sales_str', 'opp_form_views_str',
            'opp_form_favorites_str', 'opp_form_conversion_rate', 'opp_form_listing_type',
            'opp_form_aliexpress_urls', 'opp_form_notes',
            'pasted_html', 'pasted_everbee_text' # Also clear text areas
        ]
        for key in form_keys_to_clear:
            if key in st.session_state: st.session_state[key] = ""
        st.session_state.is_digital = False
        st.session_state.tags_list = []
        st.session_state.etsy_price_float = None
        st.session_state.clear_form = False # Reset the flag
        print("DEBUG: Cleared form fields at start of run.")

    st.header("Opportunity Tracker Workflow")

    # --- Prompt Generation Section ---
    with st.expander("Generate Keyword Research Prompt", expanded=False): # Keep closed
        st.subheader("Generate Keyword Research Prompt")
        # Using form keys from current app state init
        if 'prompt_niche' not in st.session_state: st.session_state.prompt_niche = ""
        if 'prompt_style' not in st.session_state: st.session_state.prompt_style = ""

        prompt_col1, prompt_col2 = st.columns(2)
        with prompt_col1:
            st.text_input("Niche Category", key="prompt_niche", placeholder="E.g., Ceramic Kitchenware")
        with prompt_col2:
            st.text_input("Style or Type Focus", key="prompt_style", placeholder="E.g., Moroccan, Decorative, Stoneware, Earthy")

        # --- Construct the Prompt (using existing corrected template code) ---
        current_niche = st.session_state.prompt_niche
        current_style = st.session_state.prompt_style
        # Define the prompt template using a standard multi-line f-string
        prompt_template = f"""You are an Etsy product market research specialist.

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

Input Parameters:
NICHE CATEGORY: {current_niche}
STYLE or TYPE FOCUS: {current_style}

Output Instructions:

Section 1: Raw Keywords Block (Pure Copy-Paste Format)
Select keywords by conceptually grouping them into three categories for research purposes: Top Broad Title Keywords, Specific Long-Tail Title Keywords, and Emerging Trend Title Keywords.
However, in the final output, do not include these category headings—just present all keywords in a single continuous list.
List all keywords (from all categories combined) one per line in a single block under the "Section 1: Raw Keywords Block" heading.
Ensure each keyword is on its own line in the rendered output by adding a double space at the end of each line in markdown (e.g., keyword  ).
Do not use commas, bullets, numbers, hyphens, or extra spaces after the keywords.
Do not wrap keywords into a paragraph—each keyword must visually appear on a new line in the rendered output.
Do not include empty lines between keywords.
Include at least 20 keyword phrases total (combined across all conceptual categories).

Section 1 Example (must match this exactly):
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

Section 2: Keyword Commentary and Thoughts
After the keywords block, explain why each keyword group is strong.

Strict commentary formatting:
Start each bullet with the keyword in Bold, then a colon :, followed by a short 1-sentence insight (max 20 words).
Do not use paragraph writing.
Do not wrap onto a second line.
Group commentary by:
Top Broad Title Keywords
Specific Long-Tail Title Keywords
Emerging Trend Title Keywords

Section 2 Example (must match this format):
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

Critical Reminders:
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
    with st.expander("1. Keyword Research Links", expanded=False): # Use state init key
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
                                cmd_args = ['chrome', '--incognito', url]
                            elif os_system == "Linux":
                                try:
                                    cmd_args = ['google-chrome', '--incognito', url]
                                    subprocess.run(['which', 'google-chrome'], check=True, capture_output=True)
                                except (FileNotFoundError, subprocess.CalledProcessError):
                                    cmd_args = ['chromium-browser', '--incognito', url]
                            if cmd_args:
                                result = subprocess.run(cmd_args, check=False, capture_output=True, text=True)
                                if result.returncode == 0:
                                    incognito_success = True; opened_incognito += 1
                                else: print(f"Incognito command failed: {result.stderr}")
                        except FileNotFoundError: print(f"Could not find browser command for incognito mode.")
                        except Exception as e: print(f"Error running incognito command: {e}")
                        if not incognito_success: # Fallback
                            try: webbrowser.open_new_tab(url)
                            except Exception as web_e: st.warning(f"Failed to open {url} in any browser: {web_e}"); continue
                        count += 1; time.sleep(1)
                if opened_incognito > 0: st.success(f"Opened {count} tabs. {opened_incognito} attempted in Chrome Incognito (check browser). Others opened in default browser.")
                else: st.success(f"Opened {count} tabs in default browser (could not use Chrome Incognito). ")
        # else: # Logic from provided code
        #     st.info("Enter some keywords above to generate Etsy search links.") # This seems unnecessary if input is empty

    # --- Opportunity Database Section ---
    st.header("2. Capture Opportunity Data")

    with st.expander("Parse Etsy Product Page HTML", expanded=False):
        st.subheader("Parse Product Page HTML")
        st.info("Go to the Etsy product page, Ctrl+A, Ctrl+C, and paste below.")
        pasted_html_input = st.text_area("Paste Full HTML Content Here:", height=200, key='pasted_html') # Use existing key
        if st.button("Parse Pasted HTML", key="parse_html_button"):
            if st.session_state.pasted_html:
                with st.spinner("Parsing HTML..."):
                    try:
                        parsed_data = parse_etsy_html_content(st.session_state.pasted_html)
                        # Update session state FORM fields from parsed Etsy data
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
                        # Append notes to FORM notes field
                        notes_key = 'opp_form_notes'
                        existing_notes = st.session_state.get(notes_key, "")
                        desc = parsed_data.get('description_notes')
                        revs = parsed_data.get('review_dates_str')
                        if desc and "--- Description ---" not in existing_notes: existing_notes += f"\n\n--- Description ---\n{desc}"
                        if revs and "--- Review Dates ---" not in existing_notes: existing_notes += f"\n\n--- Review Dates (YYYY-MM-DD) ---\n{revs}"
                        st.session_state[notes_key] = existing_notes.strip()
                        st.success("HTML Parsed and form fields updated!")

                        # --- Automatically open Everbee link ---
                        product_title_for_everbee = st.session_state.opp_form_product_title
                        if product_title_for_everbee:
                            try:
                                encoded_title = quote_plus(product_title_for_everbee)
                                everbee_url = f"https://app.everbee.io/product-analytics?search_term={encoded_title}"
                                webbrowser.open_new_tab(everbee_url)
                                st.info(f"Opened Everbee Product Analytics search for: '{product_title_for_everbee}' in a new tab.")
                                st.markdown(f"**Everbee Link:** [{everbee_url}]({everbee_url})")
                                st.info("Please copy the TEXT from the Everbee page and paste it below.") # Corrected instruction
                            except Exception as web_e:
                                st.warning(f"Could not automatically open Everbee link: {web_e}")
                                encoded_title = quote_plus(product_title_for_everbee)
                                everbee_url = f"https://app.everbee.io/product-analytics?search_term={encoded_title}"
                                st.markdown(f"**Manual Everbee Link:** [{everbee_url}]({everbee_url})")

                    except Exception as e: st.error(f"Error parsing HTML: {e}"); st.exception(e)
            else: st.warning("Please paste HTML content.")

    with st.expander("Parse Everbee Page Text", expanded=False):
        st.subheader("Parse Everbee Page Text")
        st.warning("⚠️ Everbee text parsing is fragile.")
        st.info("Paste full text content from Everbee page below.")
        pasted_everbee_input = st.text_area("Paste Full Everbee Page Text Content Here:", height=200, key='pasted_everbee_text') # Use existing key
        if st.button("Parse Everbee Text", key="parse_everbee_button"):
            if st.session_state.pasted_everbee_text:
                with st.spinner("Parsing Everbee text..."):
                    try:
                        parsed_data = parse_everbee_text_content(st.session_state.pasted_everbee_text)
                        if parsed_data:
                            # Update relevant FORM fields in session state
                            st.session_state.opp_form_product_title = parsed_data.get('product_title', st.session_state.get('opp_form_product_title',''))
                            st.session_state.opp_form_shop_name = parsed_data.get('shop_name', st.session_state.get('opp_form_shop_name',''))

                            # --- Debugging Area --- 
                            key_to_check = 'monthly_sales'
                            val_from_parsed = parsed_data.get(key_to_check, 'MISSING')
                            print(f"DEBUG State Assign: Before assign opp_form_est_sales_str: parsed_data['{key_to_check}'] = {repr(val_from_parsed)}")
                            st.session_state.opp_form_est_sales_str = str(val_from_parsed) if val_from_parsed != 'MISSING' else ''
                            print(f"DEBUG State Assign: After assign opp_form_est_sales_str: session_state value = {repr(st.session_state.opp_form_est_sales_str)}")

                            key_to_check = 'total_sales'
                            val_from_parsed = parsed_data.get(key_to_check, 'MISSING')
                            print(f"DEBUG State Assign: Before assign opp_form_total_sales_str: parsed_data['{key_to_check}'] = {repr(val_from_parsed)}")
                            st.session_state.opp_form_total_sales_str = str(val_from_parsed) if val_from_parsed != 'MISSING' else ''
                            print(f"DEBUG State Assign: After assign opp_form_total_sales_str: session_state value = {repr(st.session_state.opp_form_total_sales_str)}")

                            key_to_check = 'views'
                            val_from_parsed = parsed_data.get(key_to_check, 'MISSING')
                            print(f"DEBUG State Assign: Before assign opp_form_views_str: parsed_data['{key_to_check}'] = {repr(val_from_parsed)}")
                            st.session_state.opp_form_views_str = str(val_from_parsed) if val_from_parsed != 'MISSING' else ''
                            print(f"DEBUG State Assign: After assign opp_form_views_str: session_state value = {repr(st.session_state.opp_form_views_str)}")

                            key_to_check = 'favorites'
                            val_from_parsed = parsed_data.get(key_to_check, 'MISSING')
                            print(f"DEBUG State Assign: Before assign opp_form_favorites_str: parsed_data['{key_to_check}'] = {repr(val_from_parsed)}")
                            st.session_state.opp_form_favorites_str = str(val_from_parsed) if val_from_parsed != 'MISSING' else ''
                            print(f"DEBUG State Assign: After assign opp_form_favorites_str: session_state value = {repr(st.session_state.opp_form_favorites_str)}")
                            # --- End Debugging Area ---

                            st.session_state.opp_form_est_revenue_str = parsed_data.get('monthly_revenue_str_display', str(parsed_data.get('monthly_revenue', '')))
                            st.session_state.opp_form_conversion_rate = parsed_data.get('conversion_rate', st.session_state.get('opp_form_conversion_rate','')) # Correctly strips % in parser
                            st.session_state.opp_form_listing_age = parsed_data.get('listing_age', st.session_state.get('opp_form_listing_age',''))
                            st.session_state.opp_form_shop_age_overall = parsed_data.get('shop_age_overall', st.session_state.get('opp_form_shop_age_overall','')) # Assign parsed shop age
                            st.session_state.opp_form_category = parsed_data.get('category', st.session_state.get('opp_form_category',''))
                            st.session_state.opp_form_listing_type = parsed_data.get('listing_type', st.session_state.get('opp_form_listing_type',''))
                            st.session_state.tags_list = parsed_data.get('tags_list', []) # Update general tags list
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
                            # Append notes to FORM notes field
                            notes_key = 'opp_form_notes'
                            existing_notes = st.session_state.get(notes_key, "")
                            eb_notes = parsed_data.get('notes', '') # Get notes from parser
                            if eb_notes and "--- Everbee" not in existing_notes: # Avoid duplicates
                                existing_notes += f"\n\n{eb_notes}"
                            st.session_state[notes_key] = existing_notes.strip()
                            st.success("Everbee text parsed and form fields updated!")
                        else: st.warning("Everbee parsing failed.")
                    except Exception as e: st.error(f"Error parsing Everbee text: {e}")
            else: st.warning("Please paste Everbee text.")

    # --- Manual Entry / Review Form ---
    with st.expander("Add/Review Opportunity Details", expanded=False):
        st.subheader("Product/Shop Data")
        col1, col2 = st.columns(2)
        # Use the unique keys defined in init_session_state for all inputs here
        with col1:
            st.text_input("Product Title", key="opp_form_product_title")
            st.text_input("Product URL (Etsy - will be auto-cleaned)", key="opp_form_product_url")
            st.text_input("Shop Name", key="opp_form_shop_name")
            st.text_input("Shop URL (Etsy - will be auto-cleaned)", key="opp_form_shop_url")
            st.text_input("Price (e.g., 29.99)", key="opp_form_price_str")
            st.text_input("Est. Processing + Shipping Time (e.g., 2-8 days)", key="opp_form_processing_time") 
            st.text_input("Shipping Cost (e.g., 4.99)", key="opp_form_shipping_cost_str")
        with col2:
            st.text_input("Average Lifetime Monthly Revenue", key="opp_form_est_revenue_str")
            st.text_input("Average Lifetime Monthly Sales", key="opp_form_est_sales_str")
            st.text_input("Last 30 Days Sales (Trends)", key="opp_form_last_30_days_sales_str")
            st.text_input("Last 30 Days Revenue (Calculated)", key="opp_form_last_30_days_revenue_str", disabled=True)
            st.text_input("Listing Age (e.g., 17 months)", key="opp_form_listing_age")
            st.text_input("Shop Age Overall (e.g., 108 Mo.)", key="opp_form_shop_age_overall")
            st.text_input("Category", key="opp_form_category")
            st.text_input("Niche Tags (comma-separated)", key="opp_form_niche_tags")
            st.text_input("Total Sales (Everbee)", key="opp_form_total_sales_str")
            st.text_input("Total Views (Everbee)", key="opp_form_views_str")
            st.text_input("Total Favorites (Everbee)", key="opp_form_favorites_str")
            st.text_input("Conversion Rate (Everbee)", key="opp_form_conversion_rate")
            st.text_input("Listing Type (Everbee)", key="opp_form_listing_type")

        st.checkbox("Is Digital Product?", key="is_digital") # Use general key

        st.text_area("Potential AliExpress URLs (one per line)", key="opp_form_aliexpress_urls")
        st.text_area("Notes/Validation (Description/Reviews/Tags/Details added here)", key="opp_form_notes", height=300)

        # --- Display Parsed Everbee Tags ---
        st.subheader("Parsed Everbee Tags")
        if st.session_state.tags_list: # Use general key
            tags_df = pd.DataFrame(st.session_state.tags_list)
            display_columns = ['name', 'volume', 'competition', 'score', 'level']
            tags_df = tags_df[[col for col in display_columns if col in tags_df.columns]]
            st.dataframe(tags_df, use_container_width=True, hide_index=True)
        else: st.info("No Everbee tags parsed or available in current session.")

        # REMOVE on_click from the button below
        if st.button("Add/Update Opportunity in Database", key="add_update_opp_button"): #, on_click=clear_opportunity_form_flag):
            # --- Read data from FORM session_state keys ---
            product_title = st.session_state.opp_form_product_title
            product_url = st.session_state.opp_form_product_url
            shop_name = st.session_state.opp_form_shop_name
            shop_url = st.session_state.opp_form_shop_url 
            price_str = st.session_state.opp_form_price_str
            processing_time = st.session_state.opp_form_processing_time 
            shipping_cost_str = st.session_state.opp_form_shipping_cost_str 
            is_digital = st.session_state.is_digital # Use general key
            est_revenue_str = st.session_state.opp_form_est_revenue_str
            est_sales_str = st.session_state.opp_form_est_sales_str
            niche_tags = st.session_state.opp_form_niche_tags
            aliexpress_urls = st.session_state.opp_form_aliexpress_urls
            notes = st.session_state.opp_form_notes
            total_sales_str = st.session_state.opp_form_total_sales_str
            views_str = st.session_state.opp_form_views_str
            favorites_str = st.session_state.opp_form_favorites_str
            conversion_rate = st.session_state.opp_form_conversion_rate
            listing_age = st.session_state.opp_form_listing_age
            shop_age_overall = st.session_state.opp_form_shop_age_overall 
            category = st.session_state.opp_form_category 
            listing_type = st.session_state.opp_form_listing_type 
            everbee_tags_list = st.session_state.tags_list # Use general key
            last_30_days_sales_str = st.session_state.opp_form_last_30_days_sales_str 
            last_30_days_revenue_str = st.session_state.opp_form_last_30_days_revenue_str 

            # --- Data Validation and Type Conversion ---
            price = None; shipping_cost = None; est_revenue = None; est_sales = None
            total_sales = None; views = None; favorites = None; monthly_reviews = None
            last_30_days_sales = None; last_30_days_revenue = None
            input_valid = True

            if not all([product_title, product_url]):
                st.warning("Product Title and Product URL are required."); input_valid = False

            price, price_valid = validate_float(price_str, "Price")
            shipping_cost, sc_valid = validate_float(shipping_cost_str, "Shipping Cost")
            est_revenue, er_valid = validate_float(est_revenue_str, "Est. Monthly Revenue")
            est_sales, es_valid = validate_int(est_sales_str, "Est. Monthly Sales")
            total_sales, ts_valid = validate_int(total_sales_str, "Total Sales")
            views, v_valid = validate_int(views_str, "Total Views")
            favorites, f_valid = validate_int(favorites_str, "Total Favorites")
            # monthly_reviews is not read from form state, so skip validation
            last_30_days_sales, l30ds_valid = validate_int(last_30_days_sales_str, "Last 30 Days Sales")
            last_30_days_revenue, l30dr_valid = validate_float(last_30_days_revenue_str, "Last 30 Days Revenue")

            if not (price_valid and sc_valid and er_valid and es_valid and ts_valid and v_valid and f_valid and l30ds_valid and l30dr_valid):
                input_valid = False

            if input_valid:
                cleaned_product_url = clean_etsy_url(product_url)
                cleaned_shop_url = clean_etsy_url(shop_url)

                opportunity_data = {
                    "product_title": product_title, "price": price,
                    "product_url": cleaned_product_url, "shop_name": shop_name,
                    "shop_url": cleaned_shop_url, "niche_tags": niche_tags,
                    "est_monthly_revenue": est_revenue, "est_monthly_sales": est_sales,
                    "processing_time": processing_time, "shipping_cost": shipping_cost,
                    "aliexpress_urls": aliexpress_urls.replace('\n', ', '), "is_digital": is_digital,
                    "notes": notes, "total_sales": total_sales, "views": views,
                    "favorites": favorites, "conversion_rate": conversion_rate,
                    "listing_age": listing_age, "shop_age_overall": shop_age_overall,
                    "category": category, "listing_type": listing_type,
                    "everbee_tags": everbee_tags_list, # Pass the list directly
                    "last_30_days_sales": last_30_days_sales,
                    "last_30_days_revenue": last_30_days_revenue
                }

                inserted_id = db.add_opportunity(opportunity_data)
                if inserted_id:
                    st.success(f"Successfully added '{product_title}' (ID: {inserted_id}) to the database!")
                    # SET FLAG HERE instead of using on_click
                    st.session_state.clear_form = True 
                    # The rerun will trigger the check at the top to clear the form
                    st.rerun()
                else: st.error("Failed to add opportunity. Check if URL already exists.")

    # --- Saved Opportunities Display ---
    st.header("3. Saved Opportunities")
    with st.expander("Delete Opportunity", expanded=False):
        del_col1, del_col2 = st.columns([1, 3])
        with del_col1: id_to_delete = st.number_input("Enter ID to Delete", min_value=1, step=1, value=None, key="delete_id_input")
        with del_col2:
            st.caption(" "); st.caption(" ")
            if st.button("🗑️ Delete Opportunity by ID", key="delete_button"):
                if id_to_delete:
                    with st.spinner(f"Deleting ID: {id_to_delete}..."):
                        if db.delete_opportunity_by_id(id_to_delete): st.success(f"Deleted ID: {id_to_delete}"); st.rerun()
                        else: st.error(f"Failed to delete ID: {id_to_delete}.")
                else: st.warning("Please enter an ID.")

    # --- Fetch and Display Opportunities --- 
    opportunities_df = db.get_all_opportunities() # Moved up
    if opportunities_df is None or opportunities_df.empty:
        st.info("No opportunities saved yet or failed to load data.")
    else:
        filter_col1, filter_col2 = st.columns([1, 3]); # Basic Filtering
        with filter_col1: filter_term = st.text_input("Filter by Title/Shop/Tags")
        filtered_df = opportunities_df
        if filter_term:
            search_mask = (
                filtered_df['product_title'].str.contains(filter_term, case=False, na=False) |
                filtered_df['shop_name'].str.contains(filter_term, case=False, na=False) |
                filtered_df['niche_tags'].str.contains(filter_term, case=False, na=False)
            )
            filtered_df = filtered_df[search_mask]

        # Configure DataFrame display using keys from CURRENT init_session_state
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
                "est_monthly_revenue": st.column_config.NumberColumn("Avg Mo Rev", format="$%.0f"),
                "est_monthly_sales": st.column_config.NumberColumn("Avg Mo Sales"),
                "last_30_days_sales": st.column_config.NumberColumn("Last 30d Sales"),
                "last_30_days_revenue": st.column_config.NumberColumn("Last 30d Rev", format="$%.0f"),
                "total_sales": st.column_config.NumberColumn("Total Sales"),
                "views": st.column_config.NumberColumn("Views"),
                "favorites": st.column_config.NumberColumn("Favs"),
                "conversion_rate": st.column_config.TextColumn("Conv Rate"),
                "listing_age": st.column_config.TextColumn("Listing Age"),
                "shop_age_overall": st.column_config.TextColumn("Shop Age"),
                "category": st.column_config.TextColumn("Category", width="medium"),
                # "monthly_reviews": st.column_config.NumberColumn("Mo Revs"), # Not in current state/form
                "listing_type": st.column_config.TextColumn("Type"),
                "niche_tags": st.column_config.TextColumn("Niche Tags", width="medium"),
                "aliexpress_urls": st.column_config.TextColumn("Ali URLs", width="medium"),
                "notes": st.column_config.TextColumn("Notes", width="large"),
                "added_at": st.column_config.DatetimeColumn("Added", format="YYYY-MM-DD HH:mm"),
                "is_digital": st.column_config.CheckboxColumn("Digital?", width="small"),
                "everbee_tags": st.column_config.TextColumn("Tags Data", width="medium") # Needs formatting if list/dict
            },
            # Updated column order to match keys
            column_order=("id", "product_title", "shop_name", "price", "est_monthly_revenue", 
                          "est_monthly_sales", "last_30_days_sales", "last_30_days_revenue",
                          "total_sales", "listing_age", "shop_age_overall",
                          "category", "product_url", "shop_url", "processing_time", 
                          "shipping_cost", "views", "favorites", "conversion_rate", #"monthly_reviews", 
                          "listing_type", "is_digital", "niche_tags", 
                          "aliexpress_urls", "notes", "added_at", "everbee_tags"),
            hide_index=True,
            use_container_width=True
        )
# End of Tab 1

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
                    # 1. Parse Raw Data - returns seed, country, raw_data_list
                    extracted_seed_keyword, extracted_country_code, parsed_erank_data = parse_erank_text_content(st.session_state.pasted_erank_text)
                    
                    # Store RAW data and country for potential saving
                    st.session_state['raw_erank_data'] = parsed_erank_data
                    st.session_state['erank_country_code'] = extracted_country_code
                    
                    # Display feedback using the *parsed* value, not the widget state
                    st.info(f"Auto-detected seed keyword: '{extracted_seed_keyword or '(None Found)'}'")
                    st.info(f"Detected Country: {st.session_state.erank_country_code}")

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
                        st.session_state['erank_country_code'] = None # Clear country code too
                except Exception as e:
                    st.error(f"ERANK Analysis Error: {e}")
                    st.session_state['erank_keywords_list'] = []
                    st.session_state['raw_erank_data'] = [] # Clear raw data on error
                    st.session_state['erank_country_code'] = None # Clear country code on error
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
            # Get country code from session state (populated during analysis)
            country_code = st.session_state.get('erank_country_code', 'Unknown') 
            
            if current_raw_data:
                # Call updated db function with RAW data and country code
                saved_analysis_id = db.add_erank_analysis(seed_keyword, country_code, current_weights, current_raw_data)
                if saved_analysis_id:
                     st.success(f"Saved analysis metadata (ID: {saved_analysis_id}, Country: {country_code}) and {len(current_raw_data)} raw keywords.")
                     # Refresh the global keywords display after saving
                     st.rerun()
                else: 
                    st.error("Failed to save ERANK analysis metadata and keywords.")
            else: 
                st.warning("No raw keyword data from current session to save.")
    else:
        st.info("Paste ERANK data above and click Analyze.")

    # --- Display ALL Saved Keywords with Global Scoring --- 
    st.divider()
    st.subheader("Global Keyword Analysis (All Saved Raw Data)")
    
    all_keywords_df = db.get_all_erank_keywords() # Now includes Country column
    
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
                
                # Add Country to the display columns
                display_columns_global = [
                    'Opportunity Score', 'Keyword', 'Country', 'Added At', 'Avg Searches', 'Avg CTR', 
                    'Etsy Competition', 'Avg Clicks', 'Google Searches', 
                    'analysis_id', 'keyword_id' 
                ]
                df_display_global = df_sorted_global[[col for col in display_columns_global if col in df_sorted_global.columns]].copy() # Create copy for display formatting
                
                # Format Opportunity Score and Added At date (using .loc)
                if 'Opportunity Score' in df_display_global.columns:
                    try:
                         # Convert to numeric first, handling errors, then format
                         scores_numeric = pd.to_numeric(df_display_global['Opportunity Score'], errors='coerce')
                         df_display_global.loc[:, 'Opportunity Score'] = scores_numeric.map(lambda x: f'{x:.3f}' if pd.notna(x) else 'N/A')
                    except Exception as fmt_e: 
                        print(f"Warning: Could not format Score for global display: {fmt_e}")
                if 'Added At' in df_display_global.columns:
                    try:
                        # Use .loc; Format as YYYY-MM-DD only
                        df_display_global.loc[:, 'Added At'] = pd.to_datetime(df_display_global['Added At'], errors='coerce').dt.strftime('%Y-%m-%d') 
                    except Exception as fmt_e:
                        print(f"Warning: Could not format Added At date for global display: {fmt_e}")
                
                st.dataframe(df_display_global, use_container_width=True, hide_index=True, height=600)

            except Exception as e:
                st.error(f"Error analyzing all saved keywords: {e}")

    # --- Display Past Analysis Metadata (Optional) --- 
    with st.expander("View Past Analysis Session Metadata", expanded=False):
        past_analyses_df = db.get_all_erank_analyses() # Now includes country_code
        if past_analyses_df.empty: 
            st.info("No past analysis metadata saved.")
        else:
            display_cols_meta = ['id', 'analyzed_at', 'seed_keyword', 'country_code', 'weights'] # Add country_code here
            df_display_meta = past_analyses_df[[col for col in display_cols_meta if col in past_analyses_df.columns]]
            st.dataframe(
                df_display_meta,
                column_config={ 
                     "id": "Analysis ID", 
                     "analyzed_at": "Date", 
                     "seed_keyword": "Seed", 
                     "country_code": "Country", # Add column config
                     "weights": "Weights (JSON)" 
                 },
                hide_index=True, use_container_width=True
            )
# End of Tab 2

# ==================================== #
# === High Performing Etsy Shops Tab === #
# ==================================== #
with tab3:
    st.header("Saved Etsy Shops")

    # --- Input Section using st.form ---
    with st.form(key='save_shop_form', clear_on_submit=True):
        input_col, button_col = st.columns([3, 1])
        with input_col:
            # Define the text input widget INSIDE the form
            shop_url_input = st.text_input(
                "Enter Etsy Shop URL to save:", 
                key="saved_shop_url_input", # Key is still needed for state access
                placeholder="e.g., https://www.etsy.com/shop/ShopName"
            )
        with button_col:
            st.caption(" ") # Add space to align button vertically
            # Define the submit button INSIDE the form
            submitted = st.form_submit_button("💾 Save Shop")

        # Process form submission OUTSIDE the column definitions but INSIDE the form context
        if submitted:
            url_to_save = st.session_state.saved_shop_url_input # Access state using key
            
            # Strip whitespace FIRST
            if url_to_save: 
                url_to_save = url_to_save.strip()

            # Define the regex pattern for validation
            etsy_shop_pattern = r"https?://(?:www\.)?etsy\.com/(?:[a-z]{2}/)?shop/[A-Za-z0-9_-]+"

            if not url_to_save:
                st.warning("Please enter a shop URL.")
            # Use regex search for validation (case-insensitive)
            elif not re.search(etsy_shop_pattern, url_to_save, re.IGNORECASE):
                 st.warning("Please enter a valid Etsy shop URL (e.g., https://www.etsy.com/shop/ShopName).")
            else:
                # Clean the URL before saving (removes query params etc.)
                cleaned_url = clean_etsy_url(url_to_save) 
                print(f"DEBUG App Tab3: Attempting to save cleaned URL: {cleaned_url}")
                
                # Call the database function
                success = db.add_saved_shop(cleaned_url)
                
                if success:
                    st.success(f"Shop URL saved: {cleaned_url}")
                    # NO NEED to clear manually due to clear_on_submit=True
                    # Rerun to refresh the list displayed below the form
                    st.rerun()
                else:
                    st.warning(f"Shop URL already exists or failed to save: {cleaned_url}")

    # --- Display Section (remains outside the form) ---
    st.divider()
    st.subheader("Saved Shop List")
    saved_shops_df = db.get_all_saved_shops()

    if saved_shops_df.empty:
        st.info("No shop URLs saved yet.")
    else:
        # --- Add Shop Name column --- 
        saved_shops_df['Shop Name'] = saved_shops_df['shop_url'].apply(extract_shop_name_from_url)
        # --- End Add --- 
        
        st.dataframe(
            saved_shops_df,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "Shop Name": st.column_config.TextColumn("Shop Name", width="medium"), # Display the new column
                "shop_url": st.column_config.LinkColumn(
                    "Shop URL", 
                    display_text="🔗 Open Shop", 
                    width="large"
                ),
                "added_at": st.column_config.DatetimeColumn(
                    "Saved On",
                    format="YYYY-MM-DD HH:mm",
                    width="medium"
                )
            },
            column_order=("id", "Shop Name", "shop_url", "added_at"), # Add "Shop Name" to order
            hide_index=True,
            use_container_width=True
        )