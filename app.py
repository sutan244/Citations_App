from flask import Flask, request, render_template_string, send_file, abort, url_for, Response
import tempfile, os, time, random, uuid, threading, queue, json
import pandas as pd
from scholarly import scholarly, ProxyGenerator

app = Flask(__name__)

HTML_FORM = """
<!doctype html>
<title>Scholar -> Excel</title>
<h2>Google Scholar -> Excel export</h2>
<form id="frm" method="post" action="/start">
  Scholar ID (e.g. EJBNDEcAAAAJ): <input name=scholar_id required><br>
  Number of Year columns (e.g. 16): <input name=num_years value="16" required><br>
  <input type=submit value="Generate Excel">
</form>
<p id="note">Note: scraping can take time; progress will appear below.</p>
<div id="progress" style="white-space:pre-wrap;border:1px solid #ccc;padding:8px;height:300px;overflow:auto;"></div>
<script>
document.getElementById('frm').onsubmit = function(e){
  e.preventDefault();
  var form = e.target;
  var data = new FormData(form);
  fetch(form.action, {method: 'POST', body: data})
    .then(r => {
      if (!r.ok) return r.text().then(t => { throw new Error(t) });
      return r.json();
    })
    .then(j => {
      var jobId = j.job_id;
      var evt = new EventSource('/events/' + jobId);
      var prog = document.getElementById('progress');
      evt.onmessage = function(ev){
        try {
          var obj = JSON.parse(ev.data);
        } catch(e){
          prog.textContent += "\\n" + ev.data;
          prog.scrollTop = prog.scrollHeight;
          return;
        }
        if (obj.type === 'log') {
          prog.textContent += obj.msg + "\\n";
          prog.scrollTop = prog.scrollHeight;
        } else if (obj.type === 'done') {
          prog.textContent += "DONE. Download: " + obj.url + "\\n";
          prog.scrollTop = prog.scrollHeight;
          evt.close();
        } else if (obj.type === 'error') {
          prog.textContent += "ERROR: " + obj.msg + "\\n";
          prog.scrollTop = prog.scrollHeight;
          evt.close();
        } else if (obj.type === 'heartbeat') {
          // optional: ignore or show minimal heartbeat indicator
        }
      };
      evt.onerror = function(e){
        prog.textContent += "\\n[EventSource error]";
        evt.close();
      };
    })
    .catch(err => {
      document.getElementById('progress').textContent = 'Start error: ' + err.message;
    });
};
</script>
"""

# Small helper RNG delays
DELAY_MIN = 0.6
DELAY_MAX = 1.4
def rnd(a=DELAY_MIN, b=DELAY_MAX):
    return a + random.random() * (b - a)

# Logging queues per job
JOB_QUEUES = {}  # job_id -> Queue

def push_log(job_id, msg):
    q = JOB_QUEUES.get(job_id)
    if q:
        q.put(("log", msg))

def push_error(job_id, msg):
    q = JOB_QUEUES.get(job_id)
    if q:
        q.put(("error", msg))

def push_done(job_id, download_url):
    q = JOB_QUEUES.get(job_id)
    if q:
        q.put(("done", download_url))

# Helpers (same as your script)
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

def build_dataframe(profile_user, num_year_cols, log_fn=None, use_proxy=False):
    if use_proxy:
        try:
            pg = ProxyGenerator()
            scholarly.use_proxy(pg)
        except Exception:
            pass

    if log_fn:
        log_fn(f"Resolving author id: {profile_user} ...")
    try:
        author = scholarly.search_author_id(profile_user)
    except Exception:
        author = None
        if log_fn:
            log_fn("Direct id lookup failed; performing search_author...")
        try:
            for a in scholarly.search_author(profile_user):
                author = a
                break
        except Exception as e:
            if log_fn:
                log_fn(f"search_author error: {e}")
            author = None

    if not author:
        raise RuntimeError("Author not found.")

    if log_fn:
        log_fn("Filling author (publications)...")
    author = scholarly.fill(author, sections=["publications"])
    pubs = author.get("publications", []) or []
    if log_fn:
        log_fn(f"Found {len(pubs)} publications; fetching details...")

    rows = []
    for idx, pub in enumerate(pubs, start=1):
        if log_fn:
            log_fn(f"[{idx}/{len(pubs)}] fetching publication details...")
        try:
            pub_filled = scholarly.fill(pub)
        except Exception as e:
            if log_fn:
                log_fn(f"first fill failed: {e}; retrying shortly...")
            time.sleep(rnd(0.2, 0.6))
            try:
                pub_filled = scholarly.fill(pub)
            except Exception as e2:
                if log_fn:
                    log_fn(f"second fill failed: {e2}; skipping publication.")
                continue

        bib = pub_filled.get("bib", {}) if isinstance(pub_filled, dict) else {}
        title = bib.get("title") or pub_filled.get("title") or ""
        authors = extract_authors(bib)
        journal = extract_journal(bib)
        pub_year = extract_pub_year(bib)
        num_citations = pub_filled.get("num_citations", pub_filled.get("citedby", ""))

        cp = normalize_cites_per_year(pub_filled)
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

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_FORM)

@app.route("/start", methods=["POST"])
def start():
    scholar_id = request.form.get("scholar_id", "").strip()
    try:
        num_years = int(request.form.get("num_years", "16"))
        if num_years < 1 or num_years > 50:
            raise ValueError
    except Exception:
        return "Invalid number of years.", 400
    if not scholar_id:
        return "scholar_id required", 400

    job_id = str(uuid.uuid4())
    q = queue.Queue()
    JOB_QUEUES[job_id] = q

    def log_fn(msg):
        push_log(job_id, msg)

    def worker():
        try:
            push_log(job_id, f"Job started for {scholar_id}")
            df = build_dataframe(scholar_id, num_years, log_fn=log_fn, use_proxy=False)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            tmp_name = tmp.name
            tmp.close()
            df.to_excel(tmp_name, index=False)
            download_url = url_for('download', job_id=job_id, _external=True)
            JOB_QUEUES[job_id].tmp_path = tmp_name
            push_done(job_id, download_url)
            push_log(job_id, "File ready.")
        except Exception as e:
            push_error(job_id, str(e))
        finally:
            time.sleep(0.5)
            q.put(("__finished__", None))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    return {"job_id": job_id}, 200

@app.route("/events/<job_id>")
def events(job_id):
    if job_id not in JOB_QUEUES:
        return abort(404)
    q = JOB_QUEUES[job_id]

    def gen():
        try:
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    # heartbeat to keep connection alive through proxies
                    hb = json.dumps({"type": "heartbeat"})
                    yield f"{hb}\n\n"
                    continue

                if not item:
                    continue
                if item[0] == "__finished__":
                    break
                typ, payload = item
                if typ == "log":
                    yield f"{json.dumps({'type':'log','msg':payload})}\n\n"
                elif typ == "done":
                    yield f"{json.dumps({'type':'done','url':payload})}\n\n"
                elif typ == "error":
                    yield f"{json.dumps({'type':'error','msg':payload})}\n\n"
        finally:
            pass

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

@app.route("/download/<job_id>")
def download(job_id):
    q = JOB_QUEUES.get(job_id)
    if not q:
        return "Job not found", 404
    tmp_path = getattr(q, "tmp_path", None)
    if not tmp_path or not os.path.exists(tmp_path):
        return "File not available", 404
    def cleanup(path):
        try:
            time.sleep(5)
            os.unlink(path)
            JOB_QUEUES.pop(job_id, None)
        except Exception:
            pass
    threading.Thread(target=cleanup, args=(tmp_path,), daemon=True).start()
    return send_file(tmp_path, as_attachment=True, download_name=f"{job_id}_scholar.xlsx")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
