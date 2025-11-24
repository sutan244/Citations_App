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
from flask import Flask, request, render_template_string, redirect, url_for, send_file, jsonify

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
        .stop-btn { background-color: #f44336; }
        .stop-btn:hover { background-color: #d32f2f; }
        .viz-link { margin-top: 15px; display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }
        .viz-link:hover { background-color: #0056b3; }
    </style>
    <script>
        function stopJob(jobId) {
            fetch('/stop/' + jobId, {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('stop-btn').disabled = true;
                    document.getElementById('stop-btn').innerHTML = 'Stopping...';
                    // Refresh the page after a short delay to see the updated status
                    setTimeout(function() { 
                        window.location.href = "/status/" + jobId;
                    }, 1000);
                }
            });
            return false;
        }
    </script>
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
    <a href="{{ visualization_url }}" class="viz-link">📊 View Interactive 3D Citation Visualization</a>
    {% elif error %}
    <p class="error">❌ Error: {{ error }}</p>
    {% elif cancelled %}
    <p class="error">⚠️ Job was cancelled.</p>
    {% else %}
    <p>Job in progress... This page will refresh automatically every 3 seconds.</p>
    <button id="stop-btn" class="stop-btn" onclick="return stopJob('{{ job_id }}')">Stop Job</button>
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

# --- NEW VIZ TEMPLATE ---
VIZ_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>3D Citation Visualization</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        h1 { margin-bottom: 20px; }
        .controls { margin-bottom: 20px; }
        #chart { width: 100%; height: 800px; }
        select { padding: 10px; border-radius: 5px; border: 1px solid #ccc; }
    </style>
</head>
<body>
    <h1>Interactive 3D Citation Trend</h1>
    <div class="controls">
        <label for="paper-select">Select Individual Paper:</label>
        <select id="paper-select"></select>
        <button onclick="resetView()">Show All Papers (3D)</button>
    </div>
    <div id="chart"></div>

    <script>
        const jobId = '{{ job_id }}';
        let allData = []; // Store all citation data
        let initialLayout = {};

        function getColor(index) {
            // A simple, fixed set of colors for a diverse look
            const colors = [
                '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
            ];
            return colors[index % colors.length];
        }

        function createPlotlyData(data, selectedPaperId = null) {
            let plotData = [];
            let maxTotalCites = 0;
            if (data.length > 0) {
                 maxTotalCites = Math.max(...data.map(d => d.total_citations));
            }
            
            // X-axis (Time) is the same for all: relative year since first citation
            // Y-axis (Citations) is the actual citation count
            // Z-axis (Paper ID) separates the lines in the 3D space
            
            data.forEach((paper, index) => {
                const isSelected = selectedPaperId === paper.id;

                // For the 'scatter3d' trace
                const x = []; // Time/Relative Year
                const y = []; // Citation Count
                const z = []; // Paper ID (for separation)
                const hoverText = [];
                let cumulativeCites = 0;

                // Sort the years for continuous lines
                const sortedYears = Object.keys(paper.cites_by_year).map(Number).sort((a, b) => a - b);
                
                // Find the first citation year to calculate relative year
                let firstYearWithCites = sortedYears.find(year => paper.cites_by_year[year] > 0);
                if (!firstYearWithCites) {
                    firstYearWithCites = sortedYears.length > 0 ? sortedYears[0] : paper.pub_year;
                }
                
                const relativeStartYear = firstYearWithCites || paper.pub_year;

                sortedYears.forEach(year => {
                    const citations = paper.cites_by_year[year];
                    cumulativeCites += citations;
                    
                    x.push(year - relativeStartYear + 1); // Relative year starting at 1
                    y.push(cumulativeCites); // Cumulative citation count
                    z.push(index + 1); // Z-axis to separate lines
                    
                    // Create the detailed hover text
                    hoverText.push(
                        `<b>Title:</b> ${paper.title.substring(0, 70) + (paper.title.length > 70 ? '...' : '')}<br>` +
                        `<b>Authors:</b> ${paper.authors}<br>` +
                        `<b>Year:</b> ${year}<br>` +
                        `<b>Citations This Year:</b> ${citations}<br>` +
                        `<b>Cumulative Citations:</b> ${cumulativeCites}<br>` +
                        `<b>Total Citations:</b> ${paper.total_citations}`
                    );
                });

                const color = getColor(index);
                const trace = {
                    id: paper.id,
                    x: x,
                    y: y,
                    z: z,
                    mode: 'lines+markers',
                    name: `Paper ${index + 1}: ${paper.title.substring(0, 30)}...`,
                    type: 'scatter3d',
                    hoverinfo: 'text',
                    text: hoverText,
                    line: {
                        color: color,
                        width: isSelected ? 8 : 2 // Highlight selected line
                    },
                    marker: {
                        color: color,
                        size: isSelected ? 6 : 3,
                        symbol: 'circle'
                    },
                    opacity: isSelected ? 1.0 : (selectedPaperId ? 0.2 : 0.8) // Dim non-selected papers
                };
                plotData.push(trace);
            });
            
            // Layout Configuration for 3D
            const layout = {
                title: selectedPaperId ? `Citation Trend for Selected Paper (2D View)` : `3D Citation Trends by Relative Time (Cumulative Cites)`,
                height: 800,
                scene: {
                    aspectmode: 'manual',
                    aspectratio: {x: 1, y: 1.5, z: 0.5}, // Taller Y-axis, compressed Z-axis
                    xaxis: {
                        title: 'Relative Year (Time Axis)',
                        tickmode: 'linear',
                        dtick: 1,
                    },
                    yaxis: {
                        title: 'Cumulative Citations (Y-axis)',
                    },
                    zaxis: {
                        title: 'Paper ID (Separation Axis)',
                        tickvals: data.map((d, i) => i + 1),
                        ticktext: data.map(d => d.title.substring(0, 20) + '...'),
                        range: [0, data.length + 1],
                        autorange: false // Fixed separation
                    },
                    camera: { // Initial camera position (fixed for 3D rotation)
                        up: {x: 0, y: 0, z: 1},
                        center: {x: 0, y: 0, z: 0},
                        eye: {x: 1.25, y: 1.25, z: 1.25} // Default angle for 3D
                    }
                },
                margin: {l: 0, r: 0, b: 0, t: 30},
                hovermode: 'closest'
            };

            // If a paper is selected, switch to a 2D-like view for focus
            if (selectedPaperId) {
                const selectedIndex = data.findIndex(p => p.id === selectedPaperId);
                const eyePos = {x: 1.5, y: 1.5, z: (selectedIndex + 1) / data.length * 2}; // Position eye closer to the selected line
                
                // Redraw with just the selected paper data but in 3D space
                const selectedPaperData = plotData.filter(t => t.id === selectedPaperId);
                
                // Temporarily disable the Z-axis title/ticks for a cleaner 2D-like view
                layout.scene.zaxis.title = '';
                layout.scene.zaxis.tickvals = [(selectedIndex + 1)];
                layout.scene.zaxis.ticktext = ['Selected Paper'];
                
                // Adjust camera for a 'side' view
                layout.scene.camera.eye = {x: -2.0, y: 0.0, z: selectedIndex + 1};
                layout.scene.camera.up = {x: 0, y: 1, z: 0}; // Adjust 'up' to make it look like 2D on X-Y plane
                layout.scene.camera.center = {x: 0, y: 0, z: selectedIndex + 1};

                return { plotData: selectedPaperData, layout: layout };
            }
            
            initialLayout = layout; // Save the full 3D layout
            return { plotData: plotData, layout: layout };
        }

        function populateDropdown(data) {
            const select = document.getElementById('paper-select');
            select.innerHTML = '<option value="">-- Show All --</option>'; // Default option
            
            // Sort papers by total citations (descending)
            const sortedData = [...data].sort((a, b) => b.total_citations - a.total_citations);

            sortedData.forEach(paper => {
                const option = document.createElement('option');
                option.value = paper.id;
                option.textContent = `(${paper.total_citations} cites) ${paper.title.substring(0, 60)}...`;
                select.appendChild(option);
            });
            
            select.addEventListener('change', (e) => {
                const selectedId = e.target.value;
                if (selectedId) {
                    drawChart(selectedId);
                } else {
                    drawChart(null);
                }
            });
        }
        
        function resetView() {
            document.getElementById('paper-select').value = "";
            drawChart(null);
        }

        function drawChart(selectedPaperId = null) {
            if (allData.length === 0) {
                document.getElementById('chart').innerHTML = 'No citation data available to visualize.';
                return;
            }
            
            const { plotData, layout } = createPlotlyData(allData, selectedPaperId);

            // Re-plot the chart
            Plotly.newPlot('chart', plotData, layout);
        }

        // Fetch data and initialize
        fetch(`/api/citation_data/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('chart').innerHTML = 'Error loading data: ' + data.error;
                    return;
                }
                allData = data.citation_data.map((p, i) => ({ ...p, id: i.toString() }));
                populateDropdown(allData);
                drawChart(null);
            })
            .catch(error => {
                document.getElementById('chart').innerHTML = 'An error occurred while fetching data.';
                console.error(error);
            });

    </script>
</body>
</html>
"""
# --- END NEW VIZ TEMPLATE ---


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
            # Check if job has been cancelled
            if JOBS[job_id].get("cancelled", False):
                log("⚠️ Job cancelled by user.")
                return
                
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

        # Check if job was cancelled during processing
        if JOBS[job_id].get("cancelled", False):
            log("⚠️ Job cancelled by user.")
            return

        if not pub_citation_data:
            raise Exception("Failed to collect any publication data")
            
        # --- NEW: Save raw data for visualization ---
        JOBS[job_id]["citation_data"] = pub_citation_data
        # --- END NEW ---

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
        "filename": None,
        "citation_data": None, # New field for viz data
        "cancelled": False
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
    visualization_url = None

    if job["done"] and job["filename"]:
        download_url = url_for('download_file', job_id=job_id)
        # Check for citation data to enable the visualization link
        if job.get("citation_data"):
            visualization_url = url_for('visualization', job_id=job_id)

    return render_template_string(
        HTML,
        job_id=job_id,
        status=job["status"],
        error=job["error"],
        download_url=download_url,
        visualization_url=visualization_url, # Pass the new URL
        cancelled=job.get("cancelled", False)
    )


@app.route('/stop/<job_id>', methods=['POST'])
def stop_job(job_id):
    if job_id not in JOBS:
        return jsonify({"success": False, "message": "Job not found"}), 404
    
    # Mark the job as cancelled
    JOBS[job_id]["cancelled"] = True
    
    # Add a message to the status log
    current_status = JOBS[job_id]["status"]
    JOBS[job_id]["status"] = current_status + "\n⚠️ Cancellation requested. Stopping job..."
    
    return jsonify({"success": True, "message": "Job cancellation requested"})


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

# --- NEW VIZ ROUTES ---
@app.route('/api/citation_data/<job_id>')
def citation_data_api(job_id):
    """API endpoint to deliver citation data in JSON format for Plotly."""
    if job_id not in JOBS or not JOBS[job_id].get("citation_data"):
        return jsonify({"error": "Citation data not found for this job ID."}), 404

    # Prepare data for JSON serialization (remove keys not needed by JS or convert types)
    data = JOBS[job_id]["citation_data"]
    
    # Ensure all data is JSON-serializable (e.g., convert all int/float to string if necessary, but dicts should be fine)
    # The dictionary 'citations_by_year' contains integer keys, which are converted to strings by jsonify.
    # The JS will handle the conversion back to numbers.
    prepared_data = []
    for item in data:
        # Create a cleaner dictionary for the frontend
        prepared_data.append({
            "title": item["Title"],
            "authors": item["Authors"],
            "journal": item["Journal"],
            "pub_year": item["Year of publication"],
            "total_citations": item["TOTAL citations"],
            # Ensure keys in citations_by_year are strings for JSON
            "cites_by_year": {str(k): v for k, v in item["citations_by_year"].items()}
        })
        
    return jsonify({"citation_data": prepared_data})


@app.route('/visualization/<job_id>')
def visualization(job_id):
    """Renders the 3D visualization page."""
    if job_id not in JOBS or not JOBS[job_id].get("citation_data"):
        return "Visualization data not available. Please run the job first.", 404
        
    return render_template_string(VIZ_HTML, job_id=job_id)
# --- END NEW VIZ ROUTES ---


if __name__ == '__main__':
    app.run(debug=True)
