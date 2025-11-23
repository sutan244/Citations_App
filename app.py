from flask import Flask, request, render_template_string, send_file, abort
import tempfile, os, time, random
import pandas as pd
from scholarly import scholarly, ProxyGenerator

app = Flask(__name__)

# Template: simple form
HTML_FORM = """
<!doctype html>
<title>Scholar -> Excel</title>
<h2>Google Scholar -> Excel export</h2>
<form method=post>
  Scholar ID (e.g. EJBNDEcAAAAJ): <input name=scholar_id required><br>
  Number of Year columns (e.g. 16): <input name=num_years value="16" required><br>
  <input type=submit value="Generate Excel">
</form>
<p>Note: scraping can take time; request will block until file is ready.</p>
"""

# Small helper RNG delays similar to your original script
DELAY_MIN = 0.6
DELAY_MAX = 1.4
def rnd(a=DELAY_MIN, b=DELAY_MAX):
    return a + random.random() * (b - a)

# Helpers from your script (slightly adapted)
def normalize_cites_per_year(pub_filled):
    cp = {}
    if not isinstance(pub_filled, dict):
        return cp
    cand_keys = []
    cand_keys.append(pub_filled.get("cites_per_year"))
    cand_keys.append(pub_filled.get("citesPerYear"))
    if isinstance(pub_filled.get("bib"), dict):
        cand_keys.append(pub_filled["bib"].get("cites_per_year"))
        cand_keys.append(pub_filled["bib"].get("citesPerYear"))
    for cand in cand_keys:
        if isinstance(cand, dict):
            for k, v in cand.items():
                try:
                    ky = int(k)
                except Exception:
                    try:
                        ky = int(str(k).strip())
                    except Exception:
                        continue
                try:
                    cp[ky] = int(v)
                except Exception:
                    continue
            break
    return cp

def extract_authors(bib):
    if not isinstance(bib, dict):
        return ""
    return bib.get("author", "") or bib.get("authors", "") or ""

def extract_journal(bib):
    if not isinstance(bib, dict):
        return ""
    for k in ("journal", "venue", "publisher", "booktitle", "journal_title"):
        if k in bib and bib[k]:
            return bib[k]
    return ""

def extract_pub_year(bib):
    if not isinstance(bib, dict):
        return None
    for k in ("pub_year", "year", "publication_year"):
        if k in bib and bib[k]:
            try:
                return int(bib[k])
            except Exception:
                try:
                    return int(str(bib[k]).strip())
                except:
                    return None
    return None

def build_dataframe(profile_user, num_year_cols, use_proxy=False):
    # optional proxy initialization
    if use_proxy:
        try:
            pg = ProxyGenerator()
            scholarly.use_proxy(pg)
        except Exception:
            pass

    # attempt to resolve author
    try:
        author = scholarly.search_author_id(profile_user)
    except Exception:
        author = None
        for a in scholarly.search_author(profile_user):
            author = a
            break
    if not author:
        raise RuntimeError("Author not found.")

    author = scholarly.fill(author, sections=["publications"])
    pubs = author.get("publications", []) or []

    rows = []
    for idx, pub in enumerate(pubs, start=1):
        try:
            pub_filled = scholarly.fill(pub)
        except Exception:
            time.sleep(rnd(0.2, 0.6))
            try:
                pub_filled = scholarly.fill(pub)
            except Exception:
                continue

        bib = pub_filled.get("bib", {}) if isinstance(pub_filled, dict) else {}
        title = bib.get("title") or pub_filled.get("title") or ""
        authors = extract_authors(bib)
        journal = extract_journal(bib)
        pub_year = extract_pub_year(bib)
        num_citations = pub_filled.get("num_citations", pub_filled.get("citedby", ""))

        cp = normalize_cites_per_year(pub_filled)  # dict year->count

        # Determine start year = earliest year that has a positive (non-zero) citation count
        start_year = None
        if cp:
            years_with_cites = sorted([y for y, c in cp.items() if isinstance(c, int) and c != 0])
            if years_with_cites:
                start_year = years_with_cites[0]
            else:
                start_year = min(cp.keys())

        year_cols = {}
        total_from_yearcols = 0
        if start_year:
            for i in range(1, num_year_cols + 1):
                y = start_year + (i - 1)
                val = cp.get(y, "")
                if isinstance(val, int):
                    total_from_yearcols += val
                year_cols[f"Year {i}"] = val if val != "" else ""
        else:
            for i in range(1, num_year_cols + 1):
                year_cols[f"Year {i}"] = ""

        try:
            total_citations = int(num_citations) if num_citations not in (None, "") else total_from_yearcols
        except Exception:
            total_citations = total_from_yearcols

        row = {
            "Title": title,
            "Authors": authors,
            "Journal": journal,
            "Year of publication": pub_year if pub_year else "",
        }
        for i in range(1, num_year_cols + 1):
            row[f"Year {i}"] = year_cols.get(f"Year {i}", "")
        row["TOTAL citations"] = total_citations
        rows.append(row)

        time.sleep(rnd())

    if not rows:
        raise RuntimeError("No publications found or scraping failed.")

    cols = ["Title", "Authors", "Journal", "Year of publication"] + [f"Year {i}" for i in range(1, num_year_cols + 1)] + ["TOTAL citations"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]
    return df

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(HTML_FORM)
    # POST: generate file
    scholar_id = request.form.get("scholar_id", "").strip()
    try:
        num_years = int(request.form.get("num_years", "16"))
        if num_years < 1 or num_years > 50:
            raise ValueError
    except Exception:
        return "Invalid number of years.", 400

    if not scholar_id:
        return "scholar_id required", 400

    try:
        df = build_dataframe(scholar_id, num_years, use_proxy=False)
    except Exception as e:
        return f"Error during scraping: {e}", 500

    # write to a temp file and send it
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_name = tmp.name
    tmp.close()
    try:
        df.to_excel(tmp_name, index=False)
        return send_file(tmp_name, as_attachment=True, download_name=f"{scholar_id}_scholar.xlsx")
    finally:
        # remove file after a short delay to ensure send_file had time to stream
        # (Flask usually streams file before return completes; adjust if needed)
        def cleanup(path):
            try:
                time.sleep(5)
                os.unlink(path)
            except Exception:
                pass
        import threading
        threading.Thread(target=cleanup, args=(tmp_name,)).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)