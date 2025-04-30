# Etsy Opportunity Tracker

A simple Streamlit application to help track and validate potential Etsy dropshipping products and niches.

## Features (MVP)

*   Generate clickable Etsy search URLs from keywords.
*   Manually add potential product/shop opportunities to a local SQLite database.
*   View, sort, and filter saved opportunities.

## Setup

1.  Ensure you have Python 3.7+ installed.
2.  Create a virtual environment (optional but recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Running the App

```bash
streamlit run app.py
```

The application will open in your web browser. 