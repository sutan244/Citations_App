#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A simple web app to fetch Google Scholar publications and export to Excel.
The app automatically determines the maximum number of year columns needed.
"""
import os
import time
import random
import threading
import uuid
import pandas as pd
from scholarly import scholarly
from flask import Flask, request, render_template_string, redirect, url_for, send_file

# Create app and temp directory
app = Flask(__name__)
if not os.path.exists("tmp"):
    os.makedirs("tmp")

# Storage for job status and results
JOBS = {}

# Constants
DELAY_MIN = 0.6
DELAY_MAX = 1.4

# Simple HTML template
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Google Scholar to Excel</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        input, button { margin: 10px 0; padding: 8px; }
        input[type="text"] { width: 100%; }
        button { background-color: #4CAF50; color: white; border: none; cursor: pointer; padding: 10px 15px; }
        button:hover { background-color: #45a049; }
        .status { white-space: pre-wrap; background: #f0f0f0; padding: 10px; height: 300px; overflow-y: auto; font-family: monospace; }
        .error { color: red; }
        .success { color: green; }
    </style>
</head>
<body>
    <h1>Google Scholar → Excel Export</h1>

    <form method="POST" action="/start">
        <div>
            <label>Scholar ID (e.g., EJBNDEcAAAAJ):</label>
            <input type="text" name="scholar_id" value="EJBNDEcAAAAJ" required>
        </div>
        <p><small>The number of year columns will be determined automatically based on citation data.</small></p>
        <button type="submit">Generate Excel</button>
    </form>

    {% if job_id %}
    <h2>Job Status</h2>
    <div class="status" id="status">{{ status }}</div>

    {% if download_url %}
    <p class="success">✅ Job completed! <a href="{{ download_url }}">Download Excel File</a></p>
    {% elif error %}
    <p class="error">❌ Error: {{ error }}</p>
    {% else %}
    <p>Job in progress... This page will refresh automatically every 3 seconds.</p>
    <script>
        setTimeout(function() { 
            window.location.href = "/status/{{ job_id }}";
        }, 3000);
    </script>
    {% endif %}
    {% endif %}
</body>
</html>
"""


def random_delay(min_delay=DELAY_MIN, max_delay=DELAY_MAX):
    return min_delay + random.random() * (max_delay - min_delay)


def extract_from_bib(bib, keys, default=""):
    """Extract value from bibliography using multiple possible keys"""
    if not isinstance(bib, dict):
        return default

    for key in keys:
        if key in bib and bib[key]:
            return bib[key]
    return default


def get_cites_per_year(pub):
    """Extract and normalize citation counts per year"""
    if not isinstance(pub, dict):
        return {}

    # Try different possible locations for citation data
    for key in ["cites_per_year", "citesPerYear"]:
        if key in pub and isinstance(pub[key], dict):
            result = {}
            for year, count in pub[key].items():
                try:
                    result[int(year)] = int(count)
                except (ValueError, TypeError):
                    pass
            return result

    # Check if it's in the bib section
    if isinstance(pub.get("bib"), dict):
        for key in ["cites_per_year", "citesPerYear"]:
            if key in pub["bib"] and isinstance(pub["bib"][key], dict):
                result = {}
                for year, count in pub["bib"][key].items():
                    try:
                        result[int(year)] = int(count)
                    except (ValueError, TypeError):
                        pass
                return result

    return {}


def scrape_scholar(scholar_id, job_id):
    """Main function to scrape Google Scholar and generate Excel file"""
    logs = []

    def log(message):
        logs.append(message)
        JOBS[job_id]["status"] = "\n".join(logs)

    try:
        log(f"Starting job for Scholar ID: {scholar_id}")
        log("Searching for author profile...")

        # Find author
        try:
            author = scholarly.search_author_id(scholar_id)
        except Exception as e:
            log(f"Error finding author by ID: {e}")
            log("Trying alternative search method...")
            author = None
            for a in scholarly.search_author(scholar_id):
                author = a
                break

        if not author:
            raise Exception("Author profile not found")

        # Get publications
        log("Found author. Retrieving publication list...")
        author = scholarly.fill(author, sections=["publications"])
        pubs = author.get("publications", []) or []
        log(f"Found {len(pubs)} publications. Processing details...")

        if not pubs:
            raise Exception("No publications found for this author")

        # Process each publication and collect citation data
        rows = []
        all_citation_years = set()  # To track all years with citations across all publications
        pub_citation_data = []  # To store citation data for each publication

        for idx, pub in enumerate(pubs, start=1):
            title_preview = pub.get("bib", {}).get("title", "Unknown")[:50]
            log(f"[{idx}/{len(pubs)}] Processing: {title_preview}...")

            try:
                pub_filled = scholarly.fill(pub)
            except Exception as e:
                log(f"  ⚠️ Error retrieving details: {e}, retrying once...")
                time.sleep(random_delay(0.2, 0.6))
                try:
                    pub_filled = scholarly.fill(pub)
                except Exception as e2:
                    log(f"  ❌ Failed again: {e2}, skipping publication.")
                    continue

            # Extract basic publication details
            bib = pub_filled.get("bib", {}) if isinstance(pub_filled, dict) else {}
            title = extract_from_bib(bib, ["title"], "")
            if not title and isinstance(pub_filled, dict):
                title = pub_filled.get("title", "")

            authors = extract_from_bib(bib, ["author", "authors"], "")
            journal = extract_from_bib(bib, ["journal", "venue", "publisher", "booktitle", "journal_title"], "")

            # Extract publication year
            pub_year = None
            for year_key in ["pub_year", "year", "publication_year"]:
                if year_key in bib and bib[year_key]:
                    try:
                        pub_year = int(bib[year_key])
                        break
                    except (ValueError, TypeError):
                        try:
                            pub_year = int(str(bib[year_key]).strip())
                            break
                        except:
                            pass

            # Get citation counts by year
            citations_by_year = get_cites_per_year(pub_filled)

            # Track all years with citations
            all_citation_years.update(citations_by_year.keys())

            # Find the first year with citations
            start_year = None
            if citations_by_year:
                years_with_citations = [y for y, c in citations_by_year.items() if c > 0]
                if years_with_citations:
                    start_year = min(years_with_citations)
                else:
                    # If no citations, use earliest year in the data
                    start_year = min(citations_by_year.keys()) if citations_by_year else None

            # Get total citation count
            total_citations = pub_filled.get("num_citations", "")
            if total_citations == "":
                total_citations = pub_filled.get("citedby", "")

            total_from_years = sum(c for c in citations_by_year.values() if isinstance(c, int))

            if total_citations == "":
                total_citations = total_from_years
            else:
                try:
                    total_citations = int(total_citations)
                except (ValueError, TypeError):
                    total_citations = total_from_years

            # Store the basic publication data and citation info for later
            pub_data = {
                "Title": title,
                "Authors": authors,
                "Journal": journal,
                "Year of publication": pub_year if pub_year else "",
                "start_year": start_year,
                "citations_by_year": citations_by_year,
                "TOTAL citations": total_citations
            }
            pub_citation_data.append(pub_data)

            log(f"  ✓ Processed: {title[:50]}... (total citations: {total_citations})")
            time.sleep(random_delay())

        if not pub_citation_data:
            raise Exception("Failed to collect any publication data")

        # Determine the maximum span of years needed
        log("Analyzing citation data to determine optimal year columns...")

        if all_citation_years:
            min_citation_year = min(all_citation_years)
            max_citation_year = max(all_citation_years)
            year_span = max_citation_year - min_citation_year + 1
            log(f"Citation data spans from {min_citation_year} to {max_citation_year} ({year_span} years)")

            # Calculate the maximum number of consecutive years needed for any publication
            max_consecutive_years = 0
            for pub_data in pub_citation_data:
                if pub_data["start_year"]:
                    years_since_first_citation = max(0, max_citation_year - pub_data["start_year"] + 1)
                    max_consecutive_years = max(max_consecutive_years, years_since_first_citation)

            # Add a small buffer for future citations
            num_years = max(max_consecutive_years + 2, 5)  # At least 5 columns, plus 2 extra for future
            log(f"Using {num_years} year columns based on citation patterns")
        else:
            # Fallback if no citation data is found
            num_years = 10
            log("No citation years found. Using default of 10 year columns")

        # Now build the final dataset with the determined number of year columns
        rows = []
        for pub_data in pub_citation_data:
            row = {
                "Title": pub_data["Title"],
                "Authors": pub_data["Authors"],
                "Journal": pub_data["Journal"],
                "Year of publication": pub_data["Year of publication"],
                "TOTAL citations": pub_data["TOTAL citations"]
            }

            # Add the year columns
            start_year = pub_data["start_year"]
            citations_by_year = pub_data["citations_by_year"]

            if start_year:
                for i in range(1, num_years + 1):
                    year = start_year + (i - 1)
                    row[f"Year {i}"] = citations_by_year.get(year, "")
            else:
                for i in range(1, num_years + 1):
                    row[f"Year {i}"] = ""

            rows.append(row)

        # Create DataFrame and Excel file
        log(f"Creating Excel file with {num_years} year columns...")
        cols = ["Title", "Authors", "Journal", "Year of publication"] + \
               [f"Year {i}" for i in range(1, num_years + 1)] + \
               ["TOTAL citations"]

        df = pd.DataFrame(rows)
        for col in cols:
            if col not in df.columns:
                df[col] = ""

        df = df[cols]  # Reorder columns

        filename = f"tmp/{job_id}.xlsx"
        df.to_excel(filename, index=False)

        log(f"✅ Excel file created successfully with {len(rows)} publications and {num_years} year columns.")
        JOBS[job_id]["done"] = True
        JOBS[job_id]["filename"] = filename

    except Exception as e:
        log(f"❌ Error: {str(e)}")
        JOBS[job_id]["error"] = str(e)


# Routes
@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/start', methods=['POST'])
def start_job():
    scholar_id = request.form.get('scholar_id')

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "Initializing...",
        "done": False,
        "error": None,
        "filename": None
    }

    # Start the scraping in a background thread
    thread = threading.Thread(
        target=scrape_scholar,
        args=(scholar_id, job_id)
    )
    thread.daemon = True
    thread.start()

    return redirect(url_for('job_status', job_id=job_id))


@app.route('/status/<job_id>')
def job_status(job_id):
    if job_id not in JOBS:
        return "Job not found", 404

    job = JOBS[job_id]
    download_url = None

    if job["done"] and job["filename"]:
        download_url = url_for('download_file', job_id=job_id)

    return render_template_string(
        HTML,
        job_id=job_id,
        status=job["status"],
        error=job["error"],
        download_url=download_url
    )


@app.route('/download/<job_id>')
def download_file(job_id):
    if job_id not in JOBS or not JOBS[job_id].get("filename"):
        return "File not found", 404

    filename = JOBS[job_id]["filename"]
    if not os.path.exists(filename):
        return "File was deleted or never created", 404

    return send_file(
        filename,
        as_attachment=True,
        download_name=f"scholar_export_{job_id[:8]}.xlsx"
    )


if __name__ == '__main__':
    app.run(debug=True)
