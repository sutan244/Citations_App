#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A streamlined web app to fetch Google Scholar publications, export to Excel,
and offer 2D visualization for a single scholar and multi-author comparisons.

MODIFICATIONS:
- Year 1 per paper is now the first year the paper got cited (first year with >0 citations).
- Publications are ranked by total citations in the Excel output (added "Rank" column).
- Author summary Year 1 is the later of 1990 or the earliest citation year available.
- Multi-author mode: accepts multiple Scholar IDs/URLs.
- Multi-author Excel: Year 1 for each author corresponds to that author's own start year.
- Single Scholar Viz: Now allows checkbox selection of multiple specific papers.
- Multi Scholar Viz: Now includes a toggle to compare citations from UTD Full Articles only.
"""
import os
import time
import random
import threading
import uuid
import pandas as pd
import urllib.parse
from scholarly import scholarly
from flask import Flask, request, render_template_string, redirect, url_for, send_file, jsonify

# --- Configuration & Setup ---
app = Flask(__name__)
if not os.path.exists("tmp"):
    os.makedirs("tmp")

# Storage for job status and results
JOBS = {}

# Constants
DELAY_MIN = 0.6
DELAY_MAX = 1.4

# UTD 24 Journal List (lowercase for comparison)
UTD_24_JOURNALS = {
    "the accounting review",
    "journal of accounting and economics",
    "journal of accounting research",
    "journal of finance",
    "journal of financial economics",
    "the review of financial studies",
    "information systems research",
    "journal on computing",
    "mis quarterly",
    "journal of consumer research",
    "journal of marketing",
    "journal of marketing research",
    "marketing science",
    "management science",
    "operations research",
    "journal of operations management",
    "manufacturing and service operations management",
    "production and operations management",
    "academy of management journal",
    "academy of management review",
    "administrative science quarterly",
    "organization science",
    "journal of international business studies",
    "strategic management journal"
}

# --- HTML Templates ---
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Google Scholar to Excel</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; }
        input[type="text"], textarea { width: 100%; margin: 10px 0; padding: 8px; box-sizing: border-box; }
        button { background-color: #4CAF50; color: white; border: none; cursor: pointer; padding: 10px 15px; margin: 10px 0; }
        button:hover { background-color: #45a049; }
        .status { white-space: pre-wrap; background: #f7f9fb; padding: 10px; height: 300px; overflow-y: auto; font-family: monospace; border: 1px solid #e1e8ee; border-radius: 4px; }
        .error { color: red; }
        .success { color: green; }
        .stop-btn { background-color: #f44336; }
        .stop-btn:hover { background-color: #d32f2f; }
        .action-links a { margin-right: 15px; display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }
        .action-links a:hover { background-color: #0056b3; }
        .section { margin-top: 30px; padding: 20px; border: 1px solid #ddd; border-radius: 5px; background: #fff; }
        textarea { min-height: 90px; }
    </style>
    <script>
        function stopJob(jobId) {
            fetch('/stop/' + jobId, { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('stop-btn').disabled = true;
                    document.getElementById('stop-btn').innerHTML = 'Stopping...';
                    setTimeout(function() { window.location.href = "/status/" + jobId; }, 1000);
                }
            });
            return false;
        }
    </script>
</head>
<body>
    <h1>Google Scholar Data Extractor & Analyzer</h1>

    <div class="section">
        <h2>Single Scholar Publication Export & Individual Analysis</h2>
        <form method="POST" action="/start">
            <label>Scholar ID or Full Google Scholar URL:</label>
            <input type="text" name="scholar_id_or_url" value="EJBNDEcAAAAJ" required>
            <p><small>Example ID: EJBNDEcAAAAJ or Full URL: https://scholar.google.com/citations?user=EJBNDEcAAAAJ</small></p>
            <button type="submit">Generate Excel & Paper-Level Analysis</button>
        </form>
    </div>

    <div class="section">
        <h2>Multi-Author Summary Export & Comparison Visualization</h2>
        <form method="POST" action="/start_multi">
            <label>Enter multiple Scholar IDs or full Google Scholar URLs (comma or newline separated):</label>
            <textarea name="scholar_ids" placeholder="EJBNDEcAAAAJ, anotherID or full URLs..."></textarea>
            <p><small>Will process each author and export an Excel file with one Author Summary row per author. Also enables a comparison visualization where you can pick authors to compare.</small></p>
            <button type="submit">Process Multiple Authors & Export Summaries</button>
        </form>
    </div>

    {% if job_id %}
    <div class="section">
        <h2>Job Status: {{ job_id }}</h2>
        <div class="status" id="status">{{ status }}</div>

        {% if download_url or visualization_url %}
        <div class="action-links">
            {% if download_url %}
            <a href="{{ download_url }}">⬇️ Download Excel File</a>
            {% endif %}
            {% if visualization_url %}
            <a href="{{ visualization_url }}">📈 View Citation Trends (2D)</a>
            {% endif %}
        </div>
        <p class="success">✅ Job completed!</p>
        {% elif error %}
        <p class="error">❌ Error: {{ error }}</p>
        {% elif cancelled %}
        <p class="error">⚠️ Job was cancelled.</p>
        {% else %}
        <p>Job in progress... This page will refresh automatically every 3 seconds.</p>
        <button id="stop-btn" class="stop-btn" onclick="return stopJob('{{ job_id }}')">Stop Job</button>
        <script>
            setTimeout(function() { window.location.href = "/status/{{ job_id }}"; }, 3000);
        </script>
        {% endif %}
    </div>
    {% endif %}
</body>
</html>
"""

# 2D Viz for single author (Modified with Checkboxes)
VIZ_2D_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>2D Citation Trends Visualization</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; background: #fbfcfd; }
        h1 { margin-bottom: 10px; }
        .controls { margin-bottom: 14px; display: flex; flex-direction: column; gap: 10px;}
        .button-group { display: flex; gap: 8px; }
        #chart { width: 100%; height: 760px; }

        /* Checkbox list styling */
        .paper-list {
            height: 250px;
            overflow-y: auto;
            border: 1px solid #ccc;
            padding: 10px;
            background: #fff;
            border-radius: 5px;
        }
        .paper-item {
            display: block;
            margin-bottom: 4px;
            font-size: 0.9em;
        }
        .paper-item input { margin-right: 8px; }

        button { padding: 8px 12px; border-radius: 5px; border: none; background: #007bff; color: white; cursor: pointer; }
        button:hover { opacity: 0.95; }
        button.secondary { background: #6c757d; }
        .note { font-size: 0.95em; color: #333; margin-bottom: 8px; }
    </style>
</head>
<body>
    <h1>Paper-Level Citation Trends (2D)</h1>
    <p class="note">Left Y-axis: Citations. Right Y-axis: UTD Status (1 = Confirmed full article, 0 = Not confirmed).</p>
    <a href="/">< Back to Dashboard</a>

    <div class="controls">
        <strong>Select Papers to Visualize:</strong>
        <div class="paper-list" id="paper-list">
            Loading papers...
        </div>
        <div class="button-group">
            <button onclick="drawSelected()">Update Chart</button>
            <button class="secondary" onclick="toggleAll(true)">Select All</button>
            <button class="secondary" onclick="toggleAll(false)">Deselect All</button>
        </div>
    </div>
    <div id="chart"></div>

    <script>
        const jobId = '{{ job_id }}';
        let allData = [];

        function getColor(index) {
            const palette = ['#0057b7','#ff6f61','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'];
            return palette[index % palette.length];
        }

        function createPlotlyData(selectedPaperIds) {
            let plotData = [];
            let maxCitations = 1;

            let dataToVisualize = [];

            if (selectedPaperIds.length === 0) {
                 return { plotData: [], layout: { title: 'No papers selected' } };
            }

            dataToVisualize = allData.filter(paper => selectedPaperIds.includes(paper.id));

            if (dataToVisualize.length === 1) {
                // SINGLE PAPER VIEW (Detailed with UTD line)
                const paper = dataToVisualize[0];
                const sortedYears = Object.keys(paper.cites_by_year).map(Number).sort((a,b)=>a-b);
                const x = sortedYears;
                const y = sortedYears.map(year => paper.cites_by_year[year] || 0);
                maxCitations = Math.max(...y, 1);

                const citeTrace = {
                    x: x,
                    y: y,
                    mode: 'lines+markers',
                    name: 'Citations',
                    type: 'scatter',
                    hovertemplate: `<b>${paper.title}</b><br>Year: %{x}<br>Cites: %{y}<extra></extra>`,
                    line: { color: getColor(0), width: 3 },
                    marker: { size: 6, color: getColor(0) },
                    yaxis: 'y1'
                };
                plotData.push(citeTrace);

                const minX = x.length ? Math.min(...x) - 1 : new Date().getFullYear() - 1;
                const maxX = x.length ? Math.max(...x) + 1 : new Date().getFullYear() + 1;

                // UTD horizontal line (0 or 1)
                const utd = paper.utd_full_article === 1 ? 1 : 0;
                const utdTrace = {
                    x: [minX, maxX],
                    y: [utd, utd],
                    mode: 'lines',
                    name: 'UTD Full Article (Y2)',
                    type: 'scatter',
                    line: { color: '#d62728', dash: 'dash', width: 2 },
                    yaxis: 'y2',
                    hoverinfo: 'text',
                    text: [`UTD: ${utd}`, `UTD: ${utd}`]
                };
                plotData.push(utdTrace);

                const layout = {
                    title: `Citation Trend: ${paper.title.substring(0,80)}${paper.title.length>80?'...':''}`,
                    xaxis: { title: 'Year', tickmode: 'linear', dtick: 1, showgrid: true },
                    yaxis: { title: 'Citations in that Year', range: [0, Math.ceil(maxCitations*1.15)], gridcolor: '#e6eef8' },
                    yaxis2: {
                        title: 'UTD Status (0 or 1)',
                        overlaying: 'y',
                        side: 'right',
                        range: [-0.1, 1.1],
                        tickvals: [0,1],
                        showgrid: false
                    },
                    legend: { orientation: 'h', y: -0.15 },
                    margin: {t: 70, b: 60, l: 60, r: 80},
                    height: 720
                };
                return { plotData, layout };
            } else {
                // MULTIPLE PAPERS OVERLAY
                let maxY = 1;
                dataToVisualize.forEach((paper, idx) => {
                    const years = Object.keys(paper.cites_by_year).map(Number).sort((a,b)=>a-b);
                    if (!years.length) return;
                    const x = years;
                    const y = years.map(year => paper.cites_by_year[year] || 0);
                    maxY = Math.max(maxY, ...y);
                    plotData.push({
                        x, y, mode: 'lines', name: paper.title.substring(0,50)+'...',
                        line: { color: getColor(idx), width: 2 }, hovertemplate: `<b>${paper.title}</b><br>Year: %{x}<br>Cites: %{y}<extra></extra>`
                    });
                });

                const layout = {
                    title: `Comparison of ${dataToVisualize.length} Papers`,
                    xaxis: { title: 'Year', tickmode: 'linear', dtick: 1, showgrid: true },
                    yaxis: { title: 'Citations in that Year', range: [0, Math.ceil(maxY*1.12)], gridcolor: '#e6eef8' },
                    legend: { orientation: 'h', y: -0.15 },
                    margin: {t: 70, b: 60, l: 60, r: 20},
                    height: 720
                };
                return { plotData, layout };
            }
        }

        function populatePaperList(data) {
            const container = document.getElementById('paper-list');
            container.innerHTML = '';
            const sorted = [...data].sort((a,b)=>b.total_citations - a.total_citations);

            sorted.forEach((p, i) => {
                const pubYear = Number(p.pub_year) || new Date().getFullYear();
                const span = Math.max(1, (new Date().getFullYear() - pubYear + 1));
                const avg = (p.total_citations / span).toFixed(2);
                const utd = p.utd_full_article === 1 ? '⭐ UTD' : '';

                const label = document.createElement('label');
                label.className = 'paper-item';

                // Pre-select top 1 paper
                const checked = i === 0 ? 'checked' : '';

                label.innerHTML = `
                    <input type="checkbox" value="${p.id}" ${checked}>
                    ${utd} [#${i+1}] (${p.total_citations} cites, avg ${avg}/yr) ${p.title.substring(0,80)}${p.title.length>80?'...':''}
                `;
                container.appendChild(label);
            });
        }

        function drawSelected() {
            const checkboxes = document.querySelectorAll('#paper-list input[type="checkbox"]:checked');
            const selectedIds = Array.from(checkboxes).map(cb => cb.value);

            if (selectedIds.length === 0) {
                document.getElementById('chart').innerHTML = 'Please select at least one paper.';
                return;
            }

            const { plotData, layout } = createPlotlyData(selectedIds);
            Plotly.newPlot('chart', plotData, layout, {responsive: true});
        }

        function toggleAll(shouldSelect) {
            const checkboxes = document.querySelectorAll('#paper-list input[type="checkbox"]');
            checkboxes.forEach(cb => cb.checked = shouldSelect);
            drawSelected();
        }

        fetch(`/api/citation_data/${jobId}`)
            .then(r=>r.json())
            .then(d=>{
                if (d.error) { document.getElementById('chart').innerHTML = 'Error loading ' + d.error; return; }
                allData = d.citation_data.map((p,i)=>({...p, id: i.toString()}));
                populatePaperList(allData);
                drawSelected();
            })
            .catch(e=>{ console.error(e); document.getElementById('chart').innerHTML = 'An error occurred while fetching data.'; });
    </script>
</body>
</html>
"""

# Multi-author comparison visualization (Modified with UTD Toggle)
MULTI_VIZ_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Multi-Author Comparison Visualization</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; background: #fbfcfd; }
        h1 { margin-bottom: 10px; }
        .controls { margin-bottom: 14px; display: flex; flex-direction: column; gap: 8px;}
        #chart { width: 100%; height: 760px; }
        .author-list { max-height: 220px; overflow-y: auto; border: 1px solid #ddd; padding: 8px; border-radius: 6px; background: #fff; }
        .author-item { margin-bottom: 6px; }
        button { padding: 8px 12px; border-radius: 5px; border: none; background: #007bff; color: white; cursor: pointer; }
        button:hover { opacity: 0.95; }
        .mode-switch { margin: 10px 0; padding: 10px; background: #e9ecef; border-radius: 5px; border: 1px solid #dee2e6; display: inline-block; }
        .mode-switch label { margin-right: 20px; font-weight: bold; cursor: pointer; }
    </style>
</head>
<body>
    <h1>Multi-Author Citation Comparison (Aggregated Yearly Totals)</h1>
    <a href="/">< Back to Dashboard</a>

    <div class="controls">
        <div class="mode-switch">
            <span>Comparison Mode: </span>
            <label><input type="radio" name="mode" value="all" checked onchange="drawSelected()"> All Publications</label>
            <label><input type="radio" name="mode" value="utd" onchange="drawSelected()"> UTD Full Articles Only</label>
        </div>

        <div>
            <strong>Select authors to compare:</strong>
        </div>
        <div class="author-list" id="author-list">
            Loading authors...
        </div>
        <div>
            <button onclick="drawSelected()">Draw Selected Authors</button>
            <button onclick="drawAll()">Select All Authors</button>
        </div>
        <div id="note" style="margin-top:8px; color:#333;"></div>
    </div>
    <div id="chart"></div>

    <script>
        const jobId = '{{ job_id }}';
        let authors = []; // {id, name, years: {year: count}, start, end, agg_years_utd}

        function getColor(i){
            const palette = ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'];
            return palette[i % palette.length];
        }

        function populateAuthors(list){
            const container = document.getElementById('author-list');
            container.innerHTML = '';
            list.forEach((a, idx) => {
                const div = document.createElement('div');
                div.className = 'author-item';
                div.innerHTML = `<label><input type="checkbox" data-id="${a.id}" ${idx<3?'checked':''}/> <b>${a.name}</b> — ${a.num_publications} pubs, ${a.total_citations} cites</label>`;
                container.appendChild(div);
            });
            document.getElementById('note').innerText = 'Tip: first 3 authors are pre-checked.';
        }

        function drawSelected(){
            const checked = Array.from(document.querySelectorAll('#author-list input[type="checkbox"]:checked')).map(el => el.getAttribute('data-id'));
            drawAuthorsByIds(checked);
        }

        function drawAll(){
            document.querySelectorAll('#author-list input[type="checkbox"]').forEach(el=>el.checked=true);
            drawSelected();
        }

        function drawAuthorsByIds(ids){
            const selected = authors.filter(a=>ids.includes(a.id));
            if(!selected.length){
                document.getElementById('chart').innerHTML = 'No authors selected.';
                return;
            }

            // Check mode
            const mode = document.querySelector('input[name="mode"]:checked').value;
            const isUtdMode = (mode === 'utd');

            // Determine global year range across selected authors
            let minY = Infinity, maxY = -Infinity;
            selected.forEach(a=>{
                if(a.summary_year_start < minY) minY = a.summary_year_start;
                if(a.summary_year_end > maxY) maxY = a.summary_year_end;
            });
            if(minY === Infinity){ document.getElementById('chart').innerHTML = 'No year data.'; return; }

            const x = [];
            for(let y = minY; y<=maxY; y++) x.push(y);

            const traces = selected.map((a, idx)=>{
                // Choose data source based on mode
                const sourceData = isUtdMode ? (a.agg_years_utd || {}) : (a.agg_years || {});

                const y = x.map(year => (sourceData[year]) ? sourceData[year] : 0);

                return {
                    x, y, mode: 'lines+markers', name: a.name, line: { color: getColor(idx), width: 2 }
                };
            });

            const title = isUtdMode ? 'Author Comparison — UTD Full Articles Only' : 'Author Comparison — All Publications';

            const layout = {
                title: title,
                xaxis: { title: 'Year', tickmode: 'linear', dtick: 1 },
                yaxis: { title: 'Citations in that Year' },
                height: 720
            };

            Plotly.newPlot('chart', traces, layout, {responsive:true});
        }

        fetch(`/api/multi_summary/${jobId}`).then(r=>r.json()).then(d=>{
            if(d.error){ document.getElementById('author-list').innerHTML = 'Error: '+d.error; return; }
            authors = d.authors.map((a,i)=>({ ...a, id: String(i) }));
            populateAuthors(authors);
            drawSelected();
        }).catch(e=>{ document.getElementById('author-list').innerHTML = 'Failed to load authors.'; });
    </script>
</body>
</html>
"""


# --- Utility Functions ---

def random_delay(min_delay=DELAY_MIN, max_delay=DELAY_MAX):
    return min_delay + random.random() * (max_delay - min_delay)


def extract_from_bib(bib, keys, default=""):
    if not isinstance(bib, dict):
        return default
    for key in keys:
        if key in bib and bib[key]:
            return bib[key]
    return default


def get_cites_per_year(pub):
    if not isinstance(pub, dict):
        return {}
    for key in ["cites_per_year", "citesPerYear"]:
        if key in pub and isinstance(pub[key], dict):
            result = {}
            for year, count in pub[key].items():
                try:
                    result[int(year)] = int(count)
                except (ValueError, TypeError):
                    pass
            return result
    return {}


def extract_scholar_id(id_or_url):
    id_or_url = (id_or_url or "").strip()
    if id_or_url.startswith("http"):
        parsed_url = urllib.parse.urlparse(id_or_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        if 'user' in query_params and query_params['user']:
            return query_params['user'][0]
    return id_or_url


def normalize_journal_name(raw):
    """Strip URLs/DOIs and extraneous whitespace, lowercase."""
    if not raw:
        return ""
    s = str(raw)
    for sep in ['http://', 'https://', 'doi:', 'doi.org', 'dx.doi.org']:
        if sep in s.lower():
            parts = s.lower().split(sep, 1)
            s = parts[0]
            break
    s = s.strip().strip(',').strip()
    return s


def check_utd_journal(journal_name, pages_field=None):
    is_utd = False
    is_full_article = False

    try:
        norm = normalize_journal_name(journal_name).lower()
    except Exception:
        norm = ""

    if norm:
        for utd in UTD_24_JOURNALS:
            if utd in norm or norm in utd:
                is_utd = True
                break

    if pages_field:
        try:
            pages_str = str(pages_field).replace(' ', '').replace('.', '').replace(',', '')
            if '-' in pages_str:
                start_page_str, end_page_str = pages_str.split('-', 1)
                start = int(start_page_str)
                end = int(end_page_str)
                if end - start >= 6:
                    is_full_article = True
        except Exception:
            pass

    return is_utd, is_full_article


# --- Scraping helpers used by single and multi jobs ---

def process_author_publications(author_obj, job, job_id):
    """
    Given a scholarly author object (partially filled), process publications similarly
    to single-author flow and return:
      - pub_citation_list of per-publication dicts
      - total_utd_full_articles, total_utd_journal_pubs
      - all_citation_years (set)
    """
    pub_citation_data = []
    total_utd_full_articles = 0
    total_utd_journal_pubs = 0
    all_citation_years = set()

    pubs = author_obj.get("publications", []) or []

    for pub_idx, pub in enumerate(pubs, start=1):
        if job.get("cancelled", False):
            job["cancelled"] = True
            return pub_citation_data, total_utd_full_articles, total_utd_journal_pubs, all_citation_years

        title_preview = pub.get("bib", {}).get("title", "Unknown")[:50]
        try:
            pub_filled = scholarly.fill(pub)
        except Exception:
            time.sleep(random_delay(0.2, 0.6))
            continue

        bib = pub_filled.get("bib", {}) if isinstance(pub_filled, dict) else {}
        title = extract_from_bib(bib, ["title"], "")
        authors = extract_from_bib(bib, ["author", "authors"], "")
        journal = extract_from_bib(bib, ["journal", "venue", "publisher", "booktitle", "journal_title"], "")
        pub_year = extract_from_bib(bib, ["pub_year", "year"], "")
        total_cites = pub_filled.get("num_citations") or pub_filled.get("citedby") or 0
        pages = extract_from_bib(bib, ["pages"], "")

        is_utd_journal, is_full_article = check_utd_journal(journal, pages)
        utd_flag = 1 if is_full_article else 0

        if is_utd_journal:
            total_utd_journal_pubs += 1
        if is_full_article and is_utd_journal:
            total_utd_full_articles += 1

        citations_by_year = get_cites_per_year(pub_filled)
        all_citation_years.update(citations_by_year.keys())

        # determine paper start_year as first year the paper got cited (>0)
        start_year = None
        try:
            positive_years = sorted([y for y, c in citations_by_year.items() if c and int(c) > 0])
            if positive_years:
                start_year = int(positive_years[0])
        except Exception:
            start_year = None

        pub_data = {
            "title": title,
            "authors": authors,
            "journal": journal,
            "pub_year": pub_year,
            "total_citations": int(total_cites) if total_cites else 0,
            "start_year": start_year,
            "cites_by_year": citations_by_year,
            "utd_journal": "Yes" if is_utd_journal else "No",
            "utd_full_article": utd_flag,
        }
        pub_citation_data.append(pub_data)
        time.sleep(random_delay())

    return pub_citation_data, total_utd_full_articles, total_utd_journal_pubs, all_citation_years


# --- Main Scraping Logic (single-scholar only, unchanged) ---

def scrape_scholar(scholar_id, job_id):
    logs = []

    def log(message):
        logs.append(message)
        JOBS[job_id]["status"] = "\n".join(logs)

    try:
        log(f"Starting single job for Scholar ID: {scholar_id}")

        try:
            author = scholarly.search_author_id(scholar_id)
        except Exception as e:
            log(f"❌ Error finding author {scholar_id}: {e}")
            JOBS[job_id]["error"] = f"Error finding author: {e}"
            return

        author_name = author.get('name', scholar_id)
        log(f"Found author: {author_name}. Retrieving publications...")

        try:
            author = scholarly.fill(author, sections=["publications", "basics", "indices"], sortby='year')
        except Exception as e:
            log(f"❌ Error filling author {e}, attempting publications only.")
            try:
                author = scholarly.fill(author, sections=["publications"])
            except Exception as e2:
                log(f"❌ Failed to get publications: {e2}")
                JOBS[job_id]["error"] = f"Failed to retrieve author {e2}"
                return

        pubs = author.get("publications", []) or []
        hindex = author.get("hindex", 0)
        total_citations = author.get("citedby", 0)
        i10_index = author.get("i10index", 0)
        log(f"Found {len(pubs)} publications. H-index: {hindex}, Total Citations: {total_citations}, i10-index: {i10_index}")

        if not pubs:
            log("No publications found for this author.")
            JOBS[job_id]["error"] = "No publications found."
            return

        # Use shared helper to process publications
        pub_citation_data, total_utd_full_articles, total_utd_journal_pubs, all_citation_years = process_author_publications(
            author, JOBS[job_id], job_id)

        JOBS[job_id]["citation_data"] = pub_citation_data

        min_cite_year = min(all_citation_years) if all_citation_years else None
        max_cite_year = max(all_citation_years) if all_citation_years else int(time.strftime("%Y"))

        if min_cite_year is None:
            pub_years = []
            for p in pub_citation_data:
                try:
                    y = int(p["pub_year"])
                    pub_years.append(y)
                except Exception:
                    pass
            if pub_years:
                min_cite_year = min(pub_years)
                max_cite_year = max(pub_years)
            else:
                min_cite_year = int(time.strftime("%Y"))
                max_cite_year = min_cite_year

        agg_years = {}
        for year in range(min_cite_year, max_cite_year + 1):
            agg_years[year] = 0
        for p in pub_citation_data:
            for y, c in p["cites_by_year"].items():
                if not isinstance(y, int):
                    try:
                        y = int(y)
                    except Exception:
                        continue
                agg_years[y] = agg_years.get(y, 0) + int(c)

        summary_start_year = max(min_cite_year, 1990) if min_cite_year else 1990
        summary_end_year = max_cite_year
        if summary_end_year < summary_start_year:
            summary_end_year = summary_start_year
        summary_years_span = summary_end_year - summary_start_year + 1
        year_n_values = [agg_years.get(summary_start_year + i, 0) for i in range(summary_years_span)]

        # Build DataFrame for publications (kept for single-author Excel)
        max_consecutive_years = 0
        for data in pub_citation_data:
            if data["start_year"]:
                years_since_first_citation = max(0, max_cite_year - data["start_year"] + 1)
                max_consecutive_years = max(max_consecutive_years, years_since_first_citation)
        num_years = max(max_consecutive_years + 2, 5)

        rows = []
        for pub_data in pub_citation_data:
            row = {
                "Title": pub_data["title"],
                "Authors": pub_data["authors"],
                "Journal": pub_data["journal"],
                "Year of publication": pub_data["pub_year"],
                "UTD Journal": pub_data["utd_journal"],
                "Google Scholar i10-index (author)": int(author.get("i10index", 0) if author else 0),
                "TOTAL citations": pub_data["total_citations"]
            }
            start_year = pub_data["start_year"]
            citations_by_year = pub_data["cites_by_year"]
            if start_year:
                for i in range(1, num_years + 1):
                    year = start_year + (i - 1)
                    row[f"Year {i}"] = citations_by_year.get(year, "")
            else:
                for i in range(1, num_years + 1):
                    row[f"Year {i}"] = ""
            rows.append(row)

        df = pd.DataFrame(rows)
        if df.empty:
            log("No publication rows to write to Excel.")
            JOBS[job_id]["error"] = "No publication rows to write."
            return

        df.sort_values(by="TOTAL citations", ascending=False, inplace=True, ignore_index=True)
        df.insert(0, "Rank", range(1, len(df) + 1))

        pub_cols = ["Rank", "Title", "Authors", "Journal", "Year of publication", "UTD Journal",
                    "Google Scholar i10-index (author)"] + [f"Year {i}" for i in range(1, num_years + 1)] + [
                       "TOTAL citations"]

        for col in pub_cols:
            if col not in df.columns:
                df[col] = ""
        df = df[pub_cols]

        # Build author summary (single-line)
        summary_headers = [
                              "Name",
                              "PhD institution",
                              "PhD Year",
                              "Current institution",
                              "Rank",
                              "# Google Scholar citations",
                              "Google Scholar h-index",
                              "Google Scholar i10-index",
                              "# UT Dallas publications",
                              "Summary start year",
                              "Summary end year"
                          ] + [f"Year {i}" for i in range(1, summary_years_span + 1)]

        summary_row = {
            "Name": author_name,
            "PhD institution": "",
            "PhD Year": "",
            "Current institution": "",
            "Rank": "",
            "# Google Scholar citations": int(author.get("citedby", 0) if author else 0),
            "Google Scholar h-index": int(author.get("hindex", 0) if author else 0),
            "Google Scholar i10-index": int(author.get("i10index", 0) if author else 0),
            "# UT Dallas publications": total_utd_journal_pubs,
            "Summary start year": summary_start_year,
            "Summary end year": summary_end_year
        }

        for idx, val in enumerate(year_n_values, start=1):
            summary_row[f"Year {idx}"] = int(val)

        summary_df = pd.DataFrame([summary_row], columns=summary_headers)

        # Save Excel with Publications and Author Summary
        filename = f"tmp/{job_id}.xlsx"
        with pd.ExcelWriter(filename, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Publications", index=False)
            summary_df.to_excel(writer, sheet_name="Author Summary", index=False)

        JOBS[job_id]["filename"] = filename
        JOBS[job_id]["citation_data"] = pub_citation_data
        JOBS[job_id]["summary"] = {
            "author_name": author_name,
            "hindex": int(author.get("hindex", 0) if author else 0),
            "total_citations": int(author.get("citedby", 0) if author else 0),
            "i10index": int(author.get("i10index", 0) if author else 0),
            "total_utd_full_articles": total_utd_full_articles,
            "total_utd_journal_pubs": total_utd_journal_pubs,
            "num_publications": len(pub_citation_data),
            "summary_year_start": summary_start_year,
            "summary_year_end": summary_end_year,
            "summary_years_span": summary_years_span,
            "agg_years": agg_years
        }

        JOBS[job_id]["done"] = True
        log("Job finished successfully.")

    except Exception as e:
        log(f"❌ FATAL Error: {str(e)}")
        JOBS[job_id]["error"] = str(e)


# --- Multi-author processing job (Modified for UTD Aggregation) ---

def scrape_multiple_authors(list_of_ids, job_id):
    logs = []

    def log(message):
        logs.append(message)
        JOBS[job_id]["status"] = "\n".join(logs)

    try:
        log(f"Starting multi-author job for {len(list_of_ids)} authors.")
        summaries = []
        max_individual_span = 0

        for idx, raw in enumerate(list_of_ids, start=1):
            if JOBS[job_id].get("cancelled", False):
                log("⚠️ Job cancelled by user.")
                JOBS[job_id]["cancelled"] = True
                break

            scholar_id = extract_scholar_id(raw)
            log(f"[{idx}/{len(list_of_ids)}] Processing: {scholar_id}")

            try:
                author = scholarly.search_author_id(scholar_id)
            except Exception as e:
                log(f"  ❌ Error finding author {scholar_id}: {e}. Skipping.")
                continue

            author_name = author.get("name", scholar_id)
            log(f"  Found author: {author_name}. Retrieving publications...")
            try:
                author = scholarly.fill(author, sections=["publications", "basics", "indices"], sortby='year')
            except Exception:
                try:
                    author = scholarly.fill(author, sections=["publications"])
                except Exception as e:
                    log(f"  ❌ Failed to fill author: {e}. Skipping.")
                    continue

            pub_citation_data, total_utd_full_articles, total_utd_journal_pubs, all_citation_years = process_author_publications(
                author, JOBS[job_id], job_id)

            if JOBS[job_id].get("cancelled", False):
                log("⚠️ Job cancelled during publications retrieval.")
                break

            # determine min/max cite years for this author
            min_cite_year = min(all_citation_years) if all_citation_years else None
            max_cite_year = max(all_citation_years) if all_citation_years else int(time.strftime("%Y"))

            if min_cite_year is None:
                pub_years = []
                for p in pub_citation_data:
                    try:
                        y = int(p["pub_year"])
                        pub_years.append(y)
                    except Exception:
                        pass
                if pub_years:
                    min_cite_year = min(pub_years)
                    max_cite_year = max(pub_years)
                else:
                    min_cite_year = int(time.strftime("%Y"))
                    max_cite_year = min_cite_year

            # Build aggregated yearly totals for this author
            # Two aggregates: ALL papers, and UTD FULL papers only
            agg_years = {}
            agg_years_utd = {}

            # Initialize
            for year in range(min_cite_year, max_cite_year + 1):
                agg_years[year] = 0
                agg_years_utd[year] = 0

            for p in pub_citation_data:
                is_utd_full = (p.get("utd_full_article") == 1)

                for y, c in p["cites_by_year"].items():
                    if not isinstance(y, int):
                        try:
                            y = int(y)
                        except Exception:
                            continue

                    val = int(c)
                    # Aggregate total
                    agg_years[y] = agg_years.get(y, 0) + val

                    # Aggregate UTD only
                    if is_utd_full:
                        agg_years_utd[y] = agg_years_utd.get(y, 0) + val

            # Per-author summary start/end and span (Year 1 = summary_start_year)
            summary_start_year = max(min_cite_year, 1990) if min_cite_year else 1990
            summary_end_year = max_cite_year
            if summary_end_year < summary_start_year:
                summary_end_year = summary_start_year
            summary_years_span = summary_end_year - summary_start_year + 1

            # Track max individual span so Excel has enough Year columns
            if summary_years_span > max_individual_span:
                max_individual_span = summary_years_span

            summaries.append({
                "Name": author_name,
                "PhD institution": "",
                "PhD Year": "",
                "Current institution": "",
                "Rank": "",
                "# Google Scholar citations": int(author.get("citedby", 0) if author else 0),
                "Google Scholar h-index": int(author.get("hindex", 0) if author else 0),
                "Google Scholar i10-index": int(author.get("i10index", 0) if author else 0),
                "# UT Dallas publications": total_utd_journal_pubs,
                "Summary start year": summary_start_year,
                "Summary end year": summary_end_year,
                "summary_years_span": summary_years_span,
                "agg_years": agg_years,
                "agg_years_utd": agg_years_utd,  # Store UTD specific data
                "num_publications": len(pub_citation_data),
                "total_citations": int(author.get("citedby", 0) if author else 0)
            })

            # brief delay between authors
            time.sleep(random_delay(0.8, 1.6))

        if not summaries:
            JOBS[job_id]["error"] = "No authors processed successfully."
            log("No authors processed successfully.")
            return

        # Build Excel Author Summary rows:
        headers = [
                      "Name",
                      "PhD institution",
                      "PhD Year",
                      "Current institution",
                      "Rank",
                      "# Google Scholar citations",
                      "Google Scholar h-index",
                      "Google Scholar i10-index",
                      "# UT Dallas publications",
                      "Summary start year",
                      "Summary end year"
                  ] + [f"Year {i}" for i in range(1, max_individual_span + 1)]

        rows = []
        for s in summaries:
            row = {
                "Name": s["Name"],
                "PhD institution": s.get("PhD institution", ""),
                "PhD Year": s.get("PhD Year", ""),
                "Current institution": s.get("Current institution", ""),
                "Rank": "",
                "# Google Scholar citations": s.get("# Google Scholar citations", 0),
                "Google Scholar h-index": s.get("Google Scholar h-index", 0),
                "Google Scholar i10-index": s.get("Google Scholar i10-index", 0),
                "# UT Dallas publications": s.get("# UT Dallas publications", 0),
                "Summary start year": s.get("Summary start year", 1990),
                "Summary end year": s.get("Summary end year", int(time.strftime("%Y")))
            }
            # Fill Year 1..Year N with per-author own timeline (based on Total Cites, not UTD specific)
            agg = s.get("agg_years", {})
            start = s.get("Summary start year", 1990)
            span = s.get("summary_years_span", 1)
            for i in range(max_individual_span):
                year_label = f"Year {i + 1}"
                if i < span:
                    calendar_year = start + i
                    row[year_label] = int(agg.get(calendar_year, 0))
                else:
                    row[year_label] = ""
            rows.append(row)

        summary_df = pd.DataFrame(rows, columns=headers)

        filename = f"tmp/{job_id}_multi.xlsx"
        with pd.ExcelWriter(filename, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="Author Summaries", index=False)

        JOBS[job_id]["filename"] = filename
        JOBS[job_id]["done"] = True
        JOBS[job_id]["multi_summaries"] = summaries
        JOBS[job_id]["max_individual_span"] = max_individual_span
        JOBS[job_id]["status"] = "\n".join(logs) + "\n✅ Multi-author Excel created."

    except Exception as e:
        log(f"❌ FATAL Error: {str(e)}")
        JOBS[job_id]["error"] = str(e)


# --- Flask Routes (multi additions) ---

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/start_multi', methods=['POST'])
def start_multi():
    raw = request.form.get('scholar_ids') or ""
    # split by comma or newline
    parts = [p.strip() for p in raw.replace(',', '\n').splitlines() if p.strip()]
    if not parts:
        return "No scholar IDs provided", 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "Initializing multi-author job...",
        "done": False,
        "error": None,
        "filename": None,
        "multi_summaries": None,
        "cancelled": False
    }

    thread = threading.Thread(target=scrape_multiple_authors, args=(parts, job_id))
    thread.daemon = True
    thread.start()

    return redirect(url_for('job_status', job_id=job_id))


@app.route('/start', methods=['POST'])
def start():
    raw = request.form.get('scholar_id_or_url') or ""
    scholar_id = extract_scholar_id(raw)
    if not scholar_id:
        return "No scholar ID provided", 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": f"Initializing single-author job for {scholar_id}...",
        "done": False,
        "error": None,
        "filename": None,
        "citation_data": None,
        "summary": None,
        "cancelled": False
    }

    thread = threading.Thread(target=scrape_scholar, args=(scholar_id, job_id))
    thread.daemon = True
    thread.start()

    return redirect(url_for('job_status', job_id=job_id))


@app.route('/visualization_multi/<job_id>')
def visualization_multi(job_id):
    if job_id not in JOBS or not JOBS[job_id].get("multi_summaries"):
        return "Visualization data not available (Job either failed).", 404
    return render_template_string(MULTI_VIZ_HTML, job_id=job_id)


@app.route('/download_multi/<job_id>')
def download_multi(job_id):
    if job_id not in JOBS or not JOBS[job_id].get("filename"):
        return "File not found", 404
    filename = JOBS[job_id]["filename"]
    if not os.path.exists(filename):
        return "File not found on server", 404
    return send_file(
        filename,
        as_attachment=True,
        download_name=f"{job_id}_multi_author_summaries.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/api/multi_summary/<job_id>')
def api_multi_summary(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("multi_summaries"):
        return jsonify({"error": "Multi-author summary not found for this job."}), 404

    # Return simplified safe structure
    authors = []
    for s in job["multi_summaries"]:
        authors.append({
            "name": s.get("Name", ""),
            "num_publications": s.get("num_publications", 0),
            "total_citations": s.get("# Google Scholar citations", 0),
            "summary_year_start": s.get("Summary start year", 1990),
            "summary_year_end": s.get("Summary end year", int(time.strftime("%Y"))),
            "agg_years": s.get("agg_years", {}),
            "agg_years_utd": s.get("agg_years_utd", {})  # Expose UTD data
        })

    return jsonify({"authors": authors})


# --- Status Route ---

@app.route('/status/<job_id>')
def job_status(job_id):
    """Displays job status and links to results."""
    if job_id not in JOBS:
        return "Job not found", 404

    job = JOBS[job_id]
    download_url = None
    visualization_url = None

    if job["done"]:
        if job.get("filename"):
            # choose download/visualization depending on multi vs single
            if job.get("multi_summaries"):
                download_url = url_for('download_multi', job_id=job_id)
                visualization_url = url_for('visualization_multi', job_id=job_id)
            else:
                download_url = url_for('download_file', job_id=job_id)
                visualization_url = url_for('visualization', job_id=job_id)

    return render_template_string(
        HTML,
        job_id=job_id,
        status=job["status"],
        error=job["error"],
        download_url=download_url,
        visualization_url=visualization_url,
        cancelled=job.get("cancelled", False)
    )


# --- Single Author API & Viz ---

@app.route('/api/citation_data/<job_id>')
def api_citation_data(job_id):
    """API to serve individual paper citation data for 2D plot."""
    job = JOBS.get(job_id)
    if not job or not job.get("citation_data"):
        return jsonify({"error": "Citation data not found for this job."}), 404

    # Filter out unnecessary data and ensure JSON safety
    safe_data = [
        {k: v for k, v in pub.items() if k not in ["start_year"]}
        for pub in job["citation_data"]
    ]

    return jsonify({"citation_data": safe_data})


@app.route('/visualization/<job_id>')
def visualization(job_id):
    """Renders the 2D plot for individual paper citation trends."""
    if job_id not in JOBS or not JOBS[job_id].get("citation_data"):
        return "Visualization data not available (Job either failed).", 404

    return render_template_string(VIZ_2D_HTML, job_id=job_id)


# Download single file route
@app.route('/download/<job_id>')
def download_file(job_id):
    if job_id not in JOBS or not JOBS[job_id].get("filename"):
        return "File not found", 404
    filename = JOBS[job_id]["filename"]
    if not os.path.exists(filename):
        return "File not found on server", 404
    return send_file(
        filename,
        as_attachment=True,
        download_name=f"{job_id}_author_publications.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# Stop job endpoint (simple cancel flag)
@app.route('/stop/<job_id>', methods=['POST'])
def stop_job(job_id):
    if job_id in JOBS:
        JOBS[job_id]['cancelled'] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 404


# --- Run App ---
if __name__ == '__main__':
    app.run(debug=True, threaded=True)
