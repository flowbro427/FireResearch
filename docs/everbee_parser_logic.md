# Everbee Parser (`parse_everbee_text_content`) Logic Explanation

**Version:** As of Commit 4402922 (Post Regex Fixes, Generalized Label Matching)

## 1. Overall Goal & Philosophy

The primary goal of this parser is to reliably extract key data fields from the text content copied from an Everbee product analytics page, even when the exact formatting, line breaks, or presence/absence of specific sections varies between products or Everbee updates.

The core philosophy has shifted **away** from relying on rigid structures (like assuming a fixed number of lines per product row or expecting sections in a strict order) and **towards** a more flexible, layered approach:

1.  **Prioritize Explicit Labels:** Whenever possible, identify data by looking for known text labels (e.g., "Mo. Sales", "Listing age", "Shop Age") and extracting the value immediately following the label.
2.  **Use Heuristics Cautiously:** For data that often appears without explicit labels near the start (like Product Title, Shop Name), use simple pattern-based guesses (heuristics) but allow them to be *overwritten* if a clearly labeled value is found later.
3.  **Dedicated Section Logic:** Recognize that some parts of the Everbee output (like Trends, Tags, More Details) have relatively consistent internal structures or headers, and parse these using dedicated logic.
4.  **Employ Fallbacks:** For critical data that might be missed by label matching (like Shop Age), perform a final scan of the entire text as a safety net.
5.  **Graceful Failure:** Aim for individual components (like parsing the Tags section) to fail gracefully without halting the entire parsing process, allowing other sections to still potentially succeed.

This aims for **generalization** by not being overly dependent on exact line counts or the precise order of every single piece of data.

## 2. Preprocessing Steps

1.  **Normalize Line Breaks:** Replaces literal `\\n` sequences (often artifacts from copying) with actual newline characters (`\n`).
2.  **Split Lines:** Splits the entire text block into a list of individual lines using `splitlines()`.
3.  **Strip & Filter:** Removes leading/trailing whitespace from each line and discards any resulting empty lines. This creates the clean `lines` list used for subsequent parsing.

## 3. Flexible Boundary Detection

*   **Goal:** Attempt to identify the approximate start and end of the main product data table(s) seen visually in Everbee. This helps focus some later searches but is **not** strictly required for the parser to function.
*   **Start Detection:**
    *   Looks for common button text ("Customize button in Toolbar", etc.) within the first ~60 lines.
    *   If found, it then looks for the simple header line `"Product"` shortly after the button text.
    *   If the button/header combination isn't found, it falls back to searching for `"Product"` anywhere in the first ~60 lines.
    *   If `"Product"` is still not found, it defaults the start index to `0` but logs a warning.
*   **End Detection:**
    *   Searches for the *first* occurrence of common section headers that typically appear *after* the main table (e.g., "Listing Details", "Trends", "Tags", "Showing: \d+ of \d+", "Keyword Score", etc.) *after* the detected start index.
    *   If no end marker is found, it defaults to the end of the entire text.
*   **Flexibility:** The `table_start_index` and `table_end_index` are used primarily as *hints* or potential ranges for some logic but the core label matching (Step 5) iterates through *all* lines if needed. Parsing of dedicated sections (Trends, Tags, etc.) relies on finding *their specific headers*, not these table boundaries.

## 4. Initial Heuristics (Product Title & Shop Name)

*   **Purpose:** To capture the Product Title and Shop Name, which often appear near the top of the text *without* preceding labels like "Product:" or "Shop:".
*   **Logic:**
    1.  Looks at the first line (`lines[table_start_index]`) after the detected table start.
    2.  If this line contains letters and doesn't look like a price/number, it's *tentatively* assigned as the `product_title_heuristic`.
    3.  If a title was found, it looks at the *very next* line.
    4.  If this second line contains letters and doesn't look like a price/number/age, it's *tentatively* assigned as the `shop_name_heuristic`.
*   **Key Feature:** These heuristic values are stored *provisionally*. If the label-matching logic later finds lines explicitly matching the labels `"product"` or `"shop"`, the values found via those labels will **overwrite** these initial heuristic guesses. This prioritizes explicitly labeled data.

## 5. Core Logic: Comprehensive Label-Value Matching

*   **Purpose:** This is the primary engine for extracting data points associated with known text labels. It aims to find data regardless of its exact line number or whether it falls within the "detected" table boundaries.
*   **Mechanism:**
    *   **`label_map` Dictionary:** A central dictionary maps known labels (in lowercase) to:
        1.  The target key name in the final `parsed_data` dictionary (e.g., `"total sales"` maps to `total_sales`).
        2.  A conversion function (`safe_int`, `safe_float`, or `None` for strings).
        3.  An *optional* validation regex pattern specific to that field's expected value format.
    *   **Iteration:** Loops through every line in the `lines` list (starting after any lines processed by the heuristic).
    *   **Label Check:** Converts the current line to lowercase and checks if it exactly matches any key (label) in the `label_map`.
    *   **Noise Skip:** Explicitly skips known non-data lines like `"Dots Svg"`.
    *   **Value Extraction:** If a label is matched:
        1.  Looks at the *next* line for the potential value.
        2.  If a validation regex is defined in the `label_map` for this label, it checks if the potential value line matches that regex.
        3.  If validation passes (or no validation is needed), it proceeds. Otherwise, it logs a warning and skips assignment for this label.
    *   **Value Cleaning & Conversion:** Performs minor cleaning (like removing `%` from specific fields) and then applies the conversion function (`safe_int`/`safe_float`) if specified.
    *   **Assignment Logic:**
        *   Assigns the cleaned/converted value to the corresponding `parsed_data` key.
        *   **Overwrite Control:** Generally avoids overwriting a key if it already exists *unless* the existing value came from the initial heuristic (Title/Shop) and the new value comes from an explicit label match. This ensures labels override initial guesses. (`monthly_reviews` also allows overwrites due to multiple potential labels mapping to it).
    *   **Advancement:** If a label and valid value are processed, the loop index advances by 2 (skipping label and value lines). If only a label is found (or value fails validation), it advances by 1.

*   **Generality:** This approach handles variations because:
    *   It finds labels regardless of their absolute line number.
    *   The `label_map` acts as a flexible knowledge base of expected fields.
    *   Optional validation adds robustness against incorrect value formats.

## 6. Dedicated Section Parsing

*   **Purpose:** To handle specific blocks of text known to have unique headers and internal structures. These run *after* the main label matching.
*   **Trends:**
    *   Looks for the `"Trends"` header line.
    *   Searches within the subsequent lines (until another major section header like "Tags" or "More Details") for the specific pattern: the line `"Sales"`, followed shortly by a number, followed shortly by the line `"Revenue"`.
    *   If found, assigns the number to `last_30_days_sales`.
*   **Tags:**
    *   Looks for `"Keyword Score"` or `"Tags"` headers to find the start.
    *   Looks for `"More Details"` header to find the end.
    *   Parses the lines within this block assuming a repeating structure (Name, Volume, Competition, optional Level, Score).
    *   Appends valid tag dictionaries to the `tags_list` in `parsed_data`.
*   **More Details:**
    *   Looks for the `"More Details"` header.
    *   Parses subsequent lines as key-value pairs based on a predefined list of known keys (`known_keys` list in the code).
    *   Appends found details to the `notes` field.
    *   Crucially, only assigns `listing_type` if it wasn't already found during the main label matching phase.

*   **Generality:** Parsing these sections separately makes the overall parser less likely to break if the structure *within one section* changes slightly, as it doesn't rely on that section's data to find other sections.

## 7. Final Fallback Pass (Shop Age Overall)

*   **Purpose:** To catch the overall Shop Age if it wasn't found via the `"shop age"` label during the main matching phase. This is important because Shop Age sometimes appears without an explicit label.
*   **Logic:**
    *   Runs *only if* `shop_age_overall` is not already in `parsed_data`.
    *   Iterates through *all* lines of the text.
    *   Looks for lines matching the age pattern (`r'^(\d+\s+(?:Mo\.?|months?))$\')`.
    *   If an age pattern is found, it compares it (after normalization) to the already found `listing_age` (if any).
    *   Assigns the *first distinct* age found to `shop_age_overall` and stops searching.

*   **Generality:** Provides a safety net for a critical field that might not always be explicitly labeled.

## 8. Generality Analysis & Weaknesses

**Strengths (How it aims for Generality):**

*   **Label-Driven:** Less dependent on strict line counts or the exact order of fields within the main table area compared to previous chunking/offset methods. Finds data if the label exists.
*   **Modular Sections:** Parsing Trends, Tags, and More Details independently reduces the impact of structural changes in one section on others.
*   **Heuristic Overwriting:** Allows initial guesses but prioritizes labeled data.
*   **Fallbacks:** The final pass for Shop Age adds resilience.
*   **Noise Skipping:** Explicitly ignores known irrelevant lines (`Dots Svg`).

**Weaknesses (Potential Failure Points):**

*   **New/Changed Labels:** If Everbee changes the text of a label (e.g., "Mo. Sales" becomes "Monthly Sales"), the `label_map` needs updating.
*   **Major Layout Changes:** If the fundamental structure changes drastically (e.g., no labels are used, data is presented in a completely different format, section headers change significantly), the parser will likely fail or miss data.
*   **Ambiguous Values:** If a value line appears immediately after a label but doesn't match the expected format (due to unexpected text, symbols, or validation regex being too strict/incorrect), it will be skipped.
*   **Unlabeled Data:** If Everbee adds new important data points *without* labels, they won't be picked up by the current label-matching core. Heuristics or dedicated logic would need to be added.
*   **Heuristic Failures:** The initial Title/Shop heuristics are basic and might guess incorrectly if the initial lines are unusual.

## 9. How to Use This Document for Refinement

1.  **Document Failures:** When the parser fails on a new Everbee text paste:
    *   Paste the full text paste into a separate file or section for analysis.
    *   Note which fields were missed or incorrectly parsed.
    *   Examine the console logs (`DEBUG` messages) for that run to see *where* the logic failed (e.g., label not found, validation failed, wrong section boundary).
2.  **Identify Patterns:** Compare the failing text paste to successful ones and this explanation. Did a label change? Is data missing a label? Did the structure of a dedicated section (Trends, Tags) change? Is there new noise?
3.  **Update Logic & This Document:**
    *   If a label changed, update the `label_map` in `app.py` *and* note the change here.
    *   If validation fails, adjust the regex in the `label_map` *and* document the reason here.
    *   If a dedicated section's logic needs changing, update the code *and* the relevant section in this document.
    *   If new heuristics or fallbacks are needed, add them to the code *and* explain them here.
4.  **Goal:** Over time, this document should reflect the accumulated knowledge about Everbee's text formats and the parser's strategies, helping to make future fixes more targeted and the overall solution progressively more general.

## 10. How to Use This Document for Debugging & Refinement (For User & AI Agent)

This document serves as the definitive guide to the **intended** logic of the Everbee text parser (`parse_everbee_text_content`). When the parser fails on a new text paste, follow this systematic process:

**1. Collect Evidence:**

*   **Failing Text:** Save the **complete, raw text content** copied from the specific Everbee page that caused the failure into a temporary file (e.g., `debug_everbee_paste.txt`).
*   **Console Logs:** Run the parser with the failing text in the Streamlit app. Copy the **entire console output**, including all `DEBUG Everbee...` messages from the start to the end of the parse attempt, into another temporary file (e.g., `debug_everbee_log.txt`).
*   **Observed vs. Expected:** Note precisely which data fields are missing or have incorrect values in the Streamlit UI compared to what you see on the Everbee page.

**2. Consult This Document:**

*   Open this file (`docs/everbee_parser_logic.md`).
*   Based on the **Observed vs. Expected** errors, locate the relevant sections describing how that specific data *should* be parsed:
    *   *Missing Revenue/Sales/Views/etc.?* -> Section 5 (Label-Value Matching), check `label_map` definitions.
    *   *Missing Last 30d Sales?* -> Section 6 (Dedicated Section Parsing - Trends).
    *   *Missing Tags?* -> Section 6 (Dedicated Section Parsing - Tags).
    *   *Missing Shop Age?* -> Section 5 (label `shop age`) & Section 7 (Fallback Pass).
    *   *Wrong Title/Shop?* -> Section 4 (Heuristics) & Section 5 (Label Overwriting rules).
    *   *Other Issues?* -> Review Section 3 (Boundaries), Section 6 (More Details).

**3. Analyze the Discrepancy:**

*   **Compare Failing Text to Documentation:**
    *   Does the text in `debug_everbee_paste.txt` contain the exact label described in Section 5? (e.g., Is it "Mo. Sales" or did Everbee change it to "Monthly Sales"?)
    *   Is the value next to the label in the format expected by the validation regex (if any) defined in Section 5?
    *   Did the structure or headers of the Trends/Tags/More Details sections (Section 6) change in the failing text compared to the documented parsing logic?
    *   Is there new "noise" text (like `Dots Svg` in the past) interfering?
*   **Compare Console Logs to Documentation:**
    *   Trace the execution flow in `debug_everbee_log.txt`. Do the `DEBUG` messages show the parser attempting the steps described in the relevant section of this document?
    *   Did the logs explicitly state a failure reason? (e.g., `WARNING ... failed validation`, `label not found`, `section header not found`, `heuristic failed`, `ERROR ... No valid listing data could be parsed`). Pinpoint *where* the execution deviated from the expected path.

**4. Formulate the Fix (Provide Specific Instructions to AI Agent):**

*   **Clearly state the problem:** "Everbee parsing failed for the attached text (`debug_everbee_paste.txt`). It missed [Field Name] and got [Field Name] wrong."
*   **Reference this document and the analysis:** "According to `docs/everbee_parser_logic.md` Section [Number], it should find the label '[Expected Label]'. The logs (`debug_everbee_log.txt`) show [Specific log message indicating failure, e.g., label not found / validation failed]."
*   **Provide the evidence:** "In the failing text paste, the label is actually '[Actual Label Text]' on line [Line Number], and the value is '[Actual Value]' which [Matches/Fails] the regex '[Regex Pattern]'." OR "The 'Trends' section header seems to have changed to '[New Header]'."
*   **Propose the specific code change:** "Please modify `app.py`:
    *   Update the `label_map` in `parse_everbee_text_content`: Change the key for `[target_key]` from `'[old_label]'` to `'[new_label]'`."
    *   OR "Update the validation regex for the label `'[label_text]'` to be `r'[new_regex_pattern]'` because [reason]."
    *   OR "Modify the [Trends/Tags/More Details] parsing logic to look for the header `'[new_header]'`."
    *   OR "Add logic to skip the noise line `'[new_noise_text]'`."

**5. Update This Document:**

*   **Crucial Step!** After the code fix is successfully implemented and tested:
    *   Edit *this file* (`docs/everbee_parser_logic.md`).
    *   Add a brief note in the relevant section documenting the change and the reason.
    *   **Example:**
        *   *(In Section 5, under `label_map`)*: `Update [Date/Commit]: Added 'lifetime sales' as an alternative label mapping to `total_sales` because Everbee sometimes uses this label.`
        *   *(In Section 6, under Trends)*: `Update [Date/Commit]: Changed the Sales/Number/Revenue sequence check to allow for an optional intermediate line, as seen in [Example Product].`
*   **Commit the updated documentation** along with the code changes.

By following this process, debugging becomes more systematic, fixes become more targeted, and this document evolves into an increasingly accurate and valuable knowledge base for maintaining a robust, generalized Everbee parser. 