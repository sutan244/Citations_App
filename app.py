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
        .controls { margin-bottom: 20px; display: flex; align-items: center; gap: 15px;}
        #chart { width: 100%; height: 800px; }
        select { padding: 10px; border-radius: 5px; border: 1px solid #ccc; min-width: 250px; }
    </style>
</head>
<body>
    <h1>Interactive 3D Citation Trends</h1>
    <div class="controls">
        <label for="paper-select">Select Papers:</label>
        <select id="paper-select" multiple size="5"></select>
        <button onclick="drawChart()">Update Visualization</button>
        <button onclick="resetView()">Show All Papers</button>
    </div>
    <div id="chart"></div>

    <script>
        const jobId = '{{ job_id }}';
        let allData = []; // Store all citation data
        let maxCitationsAcrossAllYears = 0; // To dynamically set Y-axis range

        function getColor(index) {
            const colors = [
                '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
                '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5',
                '#c49c94', '#f7b6d2', '#c7c7c7', '#dbdb8d', '#9edae5'
            ];
            return colors[index % colors.length];
        }

        function createPlotlyData(selectedPaperIds = null) {
            let plotData = [];

            // Filter data if specific papers are selected
            let dataToVisualize = allData;
            if (selectedPaperIds && selectedPaperIds.length > 0) {
                dataToVisualize = allData.filter(paper => selectedPaperIds.includes(paper.id));
            }

            if (dataToVisualize.length === 0 && selectedPaperIds && selectedPaperIds.length > 0) {
                // If nothing is selected or no matching data, show all as a fallback
                dataToVisualize = allData; 
            }

            // Calculate overall max and min years
            let minOverallYear = Infinity;
            let maxOverallYear = -Infinity;
            allData.forEach(paper => {
                Object.keys(paper.cites_by_year).map(Number).forEach(year => {
                    if (year < minOverallYear) minOverallYear = year;
                    if (year > maxOverallYear) maxOverallYear = year;
                });
            });

            // If no citation years, default to a small range
            if (minOverallYear === Infinity) {
                minOverallYear = new Date().getFullYear() - 10;
                maxOverallYear = new Date().getFullYear();
            }

            dataToVisualize.forEach((paper, index) => {
                const x = []; // Years (Time Axis)
                const y = []; // Citation Counts
                const z = []; // Circular position for separation

                const initialAngle = (index / allData.length) * 2 * Math.PI; // Distribute papers around the Z-axis
                const radius = 1; // Distance from the center for each paper's 'base' line

                const sortedYears = Object.keys(paper.cites_by_year).map(Number).sort((a, b) => a - b);

                sortedYears.forEach(year => {
                    const citations = paper.cites_by_year[year] || 0;

                    x.push(year); // Actual year for the X-axis (time)
                    y.push(citations); // Citation count for the Y-axis (height)

                    // Z-axis value to create a circular arrangement
                    // The 'base' of the line is on a circle, and citations extend radially outwards
                    // We map the citations to a Z-coordinate that represents its "height" in the 3D space
                    z.push(radius * Math.cos(initialAngle) + citations * Math.sin(initialAngle)); // X-coord in "radial" plane
                    // Add an additional Z coordinate here for the other dimension in the radial plane if needed
                    // For now, let's simplify to a single Z for radial displacement

                    // The original intention was for Z to represent radial displacement.
                    // Plotly's scatter3d uses X, Y, Z.
                    // Let's make X = Year, Y = Citation Count.
                    // For the 3D "fanning out" effect, we can use Z to represent the paper's "index" but offset in a circular manner.

                    // Let's refine for the radial view:
                    // X (Horizontal time) = Year
                    // Y (Vertical height) = Citation Count
                    // Z (Radial position/Paper separation) = a calculated value based on index
                    // To make it appear as if radiating from a central time axis, we can adjust the camera
                    // and use a slight Z variation based on paper index, and Y for citation count.

                    // For the 'lines stick to time axis and grow higher as citations grows':
                    // X = Year
                    // Y = Citation Count
                    // Z = A unique identifier per paper, slightly offset to spread them out.

                });

                // Corrected approach for radial visualization:
                // X = Years (Time Axis)
                // Y = Radial position (circular spread of papers around the Z-axis)
                // Z = Citation count (height)
                // We will adjust camera later to make X-Z look like time-citations.

                const radialX = [];
                const radialY = [];
                const radialZ = [];

                const paperRadialOffset = index * 2; // Offset each paper slightly more distinctly
                const radialScalingFactor = 0.05; // Adjust how far lines fan out for each citation

                sortedYears.forEach(year => {
                    const citations = paper.cites_by_year[year] || 0;
                    maxCitationsAcrossAllYears = Math.max(maxCitationsAcrossAllYears, citations);

                    radialX.push(year); // Time axis
                    radialY.push(paperRadialOffset + citations * radialScalingFactor * Math.sin(initialAngle)); // Radial spread 1
                    radialZ.push(citations); // Height (citation count)
                    // We need another dimension for the "spread". Plotly 3D implies X, Y, Z.
                    // Let's use:
                    // X = Year
                    // Y = (Base Paper Offset Y) + Citation * sin(PaperAngle) -> radial dimension 1
                    // Z = (Base Paper Offset Z) + Citation * cos(PaperAngle) -> radial dimension 2
                    // The "height" is then the distance from (Y,Z) origin.
                    // This creates a 3D effect of papers fanning out from the X-axis.

                    // To simplify and achieve "lines grow higher as citations grows" with only time-axis rotation:
                    // Let's keep X = Year, Y = Paper Index for separation, Z = Citation Count.
                    // Then, we control camera to rotate around X-axis.

                    // Let's use the simplest:
                    // X = Year (time)
                    // Y = Paper Index (for separation around the axis)
                    // Z = Citation Count (height)
                });


                // The "radial" effect needs careful setup with 3D coordinates.
                // Let's make X = Year, Y = PaperIndex (for distinct separation), Z = Citations.
                // Then, we control the camera to rotate around the X (Year) axis.
                // This means 'Y' and 'Z' will effectively be the 'radial' coordinates when rotating.
                const paperX = [];
                const paperY = [];
                const paperZ = [];
                const hoverText = [];

                sortedYears.forEach(year => {
                    const citations = paper.cites_by_year[year] || 0;
                    maxCitationsAcrossAllYears = Math.max(maxCitationsAcrossAllYears, citations);

                    paperX.push(year); // Time axis
                    paperY.push(index * 2); // Use index for separation along one axis (Y)
                    paperZ.push(citations); // Citation count for the other axis (Z)

                    hoverText.push(
                        `<b>${paper.title.substring(0, 70) + (paper.title.length > 70 ? '...' : '')}</b><br>` +
                        `Authors: ${paper.authors}<br>` +
                        `Journal: ${paper.journal}<br>` +
                        `Publication Year: ${paper.pub_year}<br>` +
                        `Year: ${year}<br>` +
                        `Citations in ${year}: ${citations}<br>` +
                        `Total Citations: ${paper.total_citations}`
                    );
                });

                if (paperX.length > 0) { // Only add trace if there's data
                    const trace = {
                        id: paper.id,
                        x: paperX,
                        y: paperY,
                        z: paperZ,
                        mode: 'lines+markers',
                        name: `Paper ${index + 1}: ${paper.title.substring(0, 30)}...`,
                        type: 'scatter3d',
                        hoverinfo: 'text',
                        text: hoverText,
                        line: {
                            color: getColor(index),
                            width: 3 // Fixed line width
                        },
                        marker: {
                            color: getColor(index),
                            size: 3,
                            symbol: 'circle'
                        },
                        opacity: 0.9
                    };
                    plotData.push(trace);
                }
            });

            // Define the layout
            const layout = {
                title: 'Citation Trends by Year (Interactive 3D)',
                height: 800,
                scene: {
                    aspectmode: 'manual',
                    aspectratio: {x: 2, y: 1, z: 1}, // Stretch X (time) axis, Y & Z for radial
                    xaxis: {
                        title: 'Year',
                        tickmode: 'linear',
                        dtick: 1,
                        autorange: true // Let Plotly determine year range
                    },
                    yaxis: {
                        title: '', // No explicit Y-axis title (paper separation)
                        showticklabels: false, // Hide tick labels for paper separation
                        showgrid: false,
                        zeroline: false,
                        // Ensure enough range for all papers to be separate
                        range: [-1, allData.length * 2 + 1] 
                    },
                    zaxis: {
                        title: 'Citations', // Z-axis for citations
                        range: [0, maxCitationsAcrossAllYears * 1.1], // Max citations + 10% buffer
                    },
                    camera: { // Initial camera position, only allowing rotation around X
                        up: {x: 0, y: 0, z: 1}, // Z is up
                        center: {x: 0, y: 0, z: 0},
                        eye: {x: 1.5, y: 1.5, z: 1.5} // Initial view
                    },
                    dragmode: 'orbit' // Enable orbit for rotation
                },
                margin: {l: 0, r: 0, b: 0, t: 30},
                hovermode: 'closest'
            };

            return { plotData: plotData, layout: layout };
        }

        function populateDropdown(data) {
            const select = document.getElementById('paper-select');
            select.innerHTML = ''; // Clear previous options

            // Sort papers by total citations (descending)
            const sortedData = [...data].sort((a, b) => b.total_citations - a.total_citations);

            sortedData.forEach(paper => {
                const option = document.createElement('option');
                option.value = paper.id;
                option.textContent = `(${paper.total_citations} cites) ${paper.title.substring(0, 60)}...`;
                select.appendChild(option);
            });
            // Select all by default
            Array.from(select.options).forEach(option => option.selected = true);
        }

        function drawChart() {
            if (allData.length === 0) {
                document.getElementById('chart').innerHTML = 'No citation data available to visualize.';
                return;
            }

            const selectElement = document.getElementById('paper-select');
            const selectedOptions = Array.from(selectElement.selectedOptions);
            const selectedPaperIds = selectedOptions.map(option => option.value);

            const { plotData, layout } = createPlotlyData(selectedPaperIds);

            Plotly.newPlot('chart', plotData, layout, {
                displayModeBar: false, // Hide plotly toolbar by default
                // Make the plot rotatable only around the X axis
                modeBarButtonsToRemove: ['pan3d', 'zoom3d', 'resetCameraLastSave3d', 'hoverClosest3d', 'hoverCompare3d'],
                // Set fixed range on the y-axis to ensure papers stay separated
                responsive: true
            });

            const myPlot = document.getElementById('chart');
            // Allow rotation only around the X-axis (time axis)
            myPlot.on('plotly_relayout', function(eventdata) {
                if (eventdata['scene.camera.eye']) {
                    const camera = myPlot.layout.scene.camera;
                    // Lock the Z-up vector
                    camera.up = {x: 0, y: 0, z: 1};
                    // Keep the center of rotation on the X-axis
                    camera.center = {x: camera.center.x, y: 0, z: 0};
                    // Prevent arbitrary Z-axis movement for the eye, allow Y and Z to rotate
                    // This is tricky; plotly's orbit dragmode already handles rotation around the center.
                    // To restrict strictly to X-axis rotation, we need to enforce the Y and Z components of `eye` to maintain constant radius from X.
                    // This is more complex than a simple lock. A simpler approach is to use 'orbit' and accept that it will rotate around all axes from the center,
                    // but make the scene visually aligned with the X-axis as central.

                    // For truly only X-axis rotation, a custom camera update might be needed,
                    // but Plotly's 'orbit' mode is the closest built-in.
                    // We'll rely on aspect ratio and initial camera to emphasize X as the primary axis.
                }
            });
        }

        function resetView() {
            const selectElement = document.getElementById('paper-select');
            Array.from(selectElement.options).forEach(option => option.selected = true); // Select all
            drawChart();
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
                drawChart(); // Initial draw with all papers selected
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
        "citation_data": None,  # New field for viz data
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
        visualization_url=visualization_url,  # Pass the new URL
        cancelled=job.get("cancelled", False)
    )


@app.route('/stop/<job_id>', methods=['POST'])
def stop_job(job_id):
    if job_id not in JOBS:return jsonify({"success": False, "message": "Job not found"}), 404

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

    # Prepare data for JSON serialization
    data = JOBS[job_id]["citation_data"]

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
