"""
Network Idle Monitor - Dashboard Server (PostgreSQL)
Shows daily summary: total systems, idle count, active count, total idle hours.

    python3 server.py
"""

import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
import psycopg2
import psycopg2.extras

app = Flask(__name__)

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "idle_monitor"
DB_USER = "idle_user"
DB_PASS = "Password@12345"


def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS systems (
                    hostname         TEXT PRIMARY KEY,
                    ip               TEXT,
                    status           TEXT,
                    current_idle_sec INTEGER DEFAULT 0,
                    current_idle_dur TEXT DEFAULT '0s',
                    idle_since       TEXT,
                    daily_idle_sec   INTEGER DEFAULT 0,
                    daily_idle_dur   TEXT DEFAULT '0s',
                    report_date      TEXT,
                    reported_at      TEXT,
                    last_seen        TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_history (
                    id             SERIAL PRIMARY KEY,
                    hostname       TEXT,
                    ip             TEXT,
                    report_date    TEXT,
                    total_idle_sec INTEGER DEFAULT 0,
                    total_idle_dur TEXT DEFAULT '0s',
                    updated_at     TEXT,
                    UNIQUE(hostname, report_date)
                )
            """)
        conn.commit()


@app.route("/report", methods=["POST"])
def report():
    d = request.get_json()
    if not d or "hostname" not in d:
        return jsonify({"error": "bad payload"}), 400
    now = datetime.now().strftime("%H:%M:%S")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO systems
                  (hostname, ip, status, current_idle_sec, current_idle_dur,
                   idle_since, daily_idle_sec, daily_idle_dur,
                   report_date, reported_at, last_seen)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (hostname) DO UPDATE SET
                  ip               = EXCLUDED.ip,
                  status           = EXCLUDED.status,
                  current_idle_sec = EXCLUDED.current_idle_sec,
                  current_idle_dur = EXCLUDED.current_idle_dur,
                  idle_since       = EXCLUDED.idle_since,
                  daily_idle_sec   = EXCLUDED.daily_idle_sec,
                  daily_idle_dur   = EXCLUDED.daily_idle_dur,
                  report_date      = EXCLUDED.report_date,
                  reported_at      = EXCLUDED.reported_at,
                  last_seen        = EXCLUDED.last_seen
            """, (
                d.get("hostname"), d.get("ip"), d.get("status"),
                d.get("current_idle_sec", 0), d.get("current_idle_dur", "0s"),
                d.get("idle_since"), d.get("daily_idle_sec", 0),
                d.get("daily_idle_dur", "0s"), d.get("report_date"),
                d.get("reported_at"), now
            ))
            cur.execute("""
                INSERT INTO daily_history
                  (hostname, ip, report_date, total_idle_sec, total_idle_dur, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (hostname, report_date) DO UPDATE SET
                  ip             = EXCLUDED.ip,
                  -- Only update if new value is higher (prevents stale resets overwriting good data)
                  total_idle_sec = GREATEST(daily_history.total_idle_sec, EXCLUDED.total_idle_sec),
                  total_idle_dur = CASE
                      WHEN EXCLUDED.total_idle_sec >= daily_history.total_idle_sec
                      THEN EXCLUDED.total_idle_dur
                      ELSE daily_history.total_idle_dur
                  END,
                  updated_at     = EXCLUDED.updated_at
            """, (
                d.get("hostname"), d.get("ip"), d.get("report_date"),
                d.get("daily_idle_sec", 0), d.get("daily_idle_dur", "0s"),
                datetime.now().isoformat()
            ))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/systems")
def api_systems():
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM systems ORDER BY status DESC, daily_idle_sec DESC")
            rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/daily")
def api_daily():
    """Returns per-day summary: total systems, idle count, active count, total idle hours."""
    days = request.args.get("days", 7)
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    report_date,
                    COUNT(DISTINCT hostname)                          AS total_systems,
                    COUNT(DISTINCT CASE WHEN total_idle_sec > 0
                          THEN hostname END)                          AS idle_systems,
                    COUNT(DISTINCT CASE WHEN total_idle_sec = 0
                          THEN hostname END)                          AS active_systems,
                    SUM(total_idle_sec)                               AS total_idle_sec,
                    ROUND(SUM(total_idle_sec) / 3600.0, 2)            AS total_idle_hours
                FROM daily_history
                WHERE report_date::date >= CURRENT_DATE - INTERVAL %s
                GROUP BY report_date
                ORDER BY report_date DESC
            """, (f"{days} days",))
            summary = cur.fetchall()

            cur.execute("""
                SELECT hostname, ip, report_date, total_idle_sec, total_idle_dur
                FROM daily_history
                WHERE report_date::date >= CURRENT_DATE - INTERVAL %s
                ORDER BY report_date DESC, total_idle_sec DESC
            """, (f"{days} days",))
            detail = cur.fetchall()

    return jsonify({
        "summary": [dict(r) for r in summary],
        "detail":  [dict(r) for r in detail]
    })


@app.route("/api/export")
def api_export():
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT report_date,
                       COUNT(DISTINCT hostname)                       AS total_systems,
                       COUNT(DISTINCT CASE WHEN total_idle_sec > 0
                             THEN hostname END)                       AS idle_systems,
                       COUNT(DISTINCT CASE WHEN total_idle_sec = 0
                             THEN hostname END)                       AS active_systems,
                       ROUND(SUM(total_idle_sec)/3600.0,2)            AS total_idle_hours
                FROM daily_history
                GROUP BY report_date
                ORDER BY report_date DESC
            """)
            rows = cur.fetchall()
    lines = ["Date,Total Systems,Idle Systems,Active Systems,Total Idle Hours"]
    for r in rows:
        lines.append(",".join(str(v or "") for v in dict(r).values()))
    return Response(
        "\n".join(lines), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=idle_daily_report.csv"}
    )


@app.route("/")
def index():
    return render_template_string(HTML)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Idle Monitor</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f4f3f0;--card:#fff;--border:#e3e1d9;
  --text:#1c1c1a;--muted:#6a6a66;--hint:#9a9892;
  --idle:#dc2626;--idle-bg:#fef2f2;--idle-bd:#fecaca;
  --active:#16a34a;--active-bg:#f0fdf4;--active-bd:#bbf7d0;
  --blue:#2563eb;--blue-bg:#eff6ff;
  --amber:#d97706;--amber-bg:#fffbeb;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#181816;--card:#222220;--border:#36362c;
  --text:#e6e4dc;--muted:#9a9892;--hint:#6a6a66;
  --idle:#f87171;--idle-bg:#2c1414;--idle-bd:#7f1d1d;
  --active:#4ade80;--active-bg:#0c2b15;--active-bd:#14532d;
  --blue:#60a5fa;--blue-bg:#1a2a42;
  --amber:#fbbf24;--amber-bg:#2d2010;
}}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
.page{max-width:1200px;margin:0 auto;padding:20px 16px}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.brand{display:flex;align-items:center;gap:8px}
h1{font-size:17px;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:var(--active);animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.actions{display:flex;gap:8px;align-items:center}
.btn{padding:6px 13px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:12px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
.btn:hover{background:var(--border)}
.ts{font-size:11px;color:var(--muted)}
select{padding:5px 10px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:12px}

/* summary cards */
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px}
.card-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.card-val{font-size:30px;font-weight:600;line-height:1}
.v-total{color:var(--text)}
.v-idle{color:var(--idle)}
.v-active{color:var(--active)}
.v-hours{color:var(--amber)}
.card-sub{font-size:11px;color:var(--muted);margin-top:4px}

/* tabs */
.tabs{display:flex;gap:2px;margin-bottom:16px;border-bottom:2px solid var(--border)}
.tab{padding:8px 18px;font-size:13px;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}
.tab.on{color:var(--blue);border-bottom-color:var(--blue)}
.tab-panel{display:none}
.tab-panel.on{display:block}

/* table */
.toolbar{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center}
.search{flex:1;min-width:160px;padding:6px 10px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:12px;outline:none}
.search:focus{border-color:var(--blue)}
.tbl-wrap{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{padding:9px 12px;text-align:left;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--border);background:var(--bg);white-space:nowrap}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--bg)}
td{padding:10px 12px;white-space:nowrap}
.hn{font-family:monospace;font-weight:600;font-size:12px}
.ip{color:var(--muted);font-family:monospace;font-size:11px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.pill-idle{background:var(--idle-bg);color:var(--idle);border:1px solid var(--idle-bd)}
.pill-active{background:var(--active-bg);color:var(--active);border:1px solid var(--active-bd)}
.dur-idle{color:var(--idle);font-weight:600}
.dur-day{color:var(--amber);font-weight:500}
.dur-hours{color:var(--amber);font-weight:600}
.date-badge{display:inline-block;padding:2px 8px;border-radius:5px;background:var(--blue-bg);color:var(--blue);font-size:11px;font-weight:600}
.empty{padding:50px;text-align:center;color:var(--muted)}
.footer{margin-top:8px;display:flex;justify-content:space-between;font-size:11px;color:var(--hint);flex-wrap:wrap;gap:6px}

/* day summary cards */
.day-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:16px}
.day-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.day-card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.day-card-date{font-weight:600;font-size:13px}
.day-card-hours{font-size:20px;font-weight:600;color:var(--amber)}
.day-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.day-stat{text-align:center}
.day-stat-val{font-size:18px;font-weight:600}
.day-stat-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.bar-bg{height:5px;background:var(--border);border-radius:3px;overflow:hidden;margin-top:8px}
.bar-fill{height:100%;border-radius:3px;background:var(--idle);transition:width .5s}
</style>
</head>
<body>
<div class="page">

  <div class="topbar">
    <div class="brand"><span class="dot"></span><h1>Network Idle Monitor</h1></div>
    <div class="actions">
      <span class="ts" id="ts">Loading...</span>
      <button class="btn" onclick="loadAll()">&#8635; Refresh</button>
      <a class="btn" href="/api/export">&#8595; Export CSV</a>
    </div>
  </div>

  <!-- Today summary cards -->
  <div class="cards" id="today-cards">
    <div class="card"><div class="card-label">Total Systems</div><div class="card-val v-total" id="c-total">—</div><div class="card-sub">today</div></div>
    <div class="card"><div class="card-label">Idle Systems</div><div class="card-val v-idle" id="c-idle">—</div><div class="card-sub" id="c-idle-pct">—</div></div>
    <div class="card"><div class="card-label">Active Systems</div><div class="card-val v-active" id="c-active">—</div><div class="card-sub" id="c-active-pct">—</div></div>
    <div class="card"><div class="card-label">Total Idle Hours</div><div class="card-val v-hours" id="c-hours">—</div><div class="card-sub">all systems combined</div></div>
  </div>

  <div class="tabs">
    <div class="tab on" onclick="switchTab('live',this)">Live Status</div>
    <div class="tab"    onclick="switchTab('daily',this)">Day-wise Summary</div>
    <div class="tab"    onclick="switchTab('detail',this)">Per System History</div>
  </div>

  <!-- LIVE TAB -->
  <div class="tab-panel on" id="tab-live">
    <div class="toolbar">
      <input class="search" id="search-live" placeholder="Search hostname or IP..." oninput="renderLive()">
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Hostname</th>
          <th>IP Address</th>
          <th>Status</th>
          <th>Current Idle</th>
          <th>Idle Since</th>
          <th>Today Total Idle</th>
          <th>Last Report</th>
        </tr></thead>
        <tbody id="tbody-live"></tbody>
      </table>
    </div>
    <div class="footer">
      <span>Idle = screen locked (Win+L) · Auto-refreshes every 60s</span>
      <span id="foot-live"></span>
    </div>
  </div>

  <!-- DAILY SUMMARY TAB -->
  <div class="tab-panel" id="tab-daily">
    <div class="toolbar">
      <select id="days-select" onchange="loadDaily()">
        <option value="7">Last 7 days</option>
        <option value="14">Last 14 days</option>
        <option value="30">Last 30 days</option>
      </select>
    </div>
    <div class="day-cards" id="day-cards"></div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Date</th>
          <th>Total Systems</th>
          <th>Idle Systems</th>
          <th>Active Systems</th>
          <th>Total Idle Hours</th>
          <th>Idle %</th>
        </tr></thead>
        <tbody id="tbody-daily"></tbody>
      </table>
    </div>
  </div>

  <!-- PER SYSTEM HISTORY TAB -->
  <div class="tab-panel" id="tab-detail">
    <div class="toolbar">
      <input class="search" id="search-detail" placeholder="Search hostname..." oninput="renderDetail()">
      <select id="days-select2" onchange="loadDaily()">
        <option value="7">Last 7 days</option>
        <option value="14">Last 14 days</option>
        <option value="30">Last 30 days</option>
      </select>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Date</th>
          <th>Hostname</th>
          <th>IP Address</th>
          <th>Total Idle Time</th>
        </tr></thead>
        <tbody id="tbody-detail"></tbody>
      </table>
    </div>
    <div class="footer"><span id="foot-detail"></span></div>
  </div>

</div>
<script>
let allSystems=[], dailySummary=[], dailyDetail=[];

async function loadAll(){
  await Promise.all([loadSystems(), loadDaily()]);
  document.getElementById('ts').textContent =
    'Updated ' + new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

async function loadSystems(){
  try{
    const r = await fetch('/api/systems');
    allSystems = await r.json();
    renderTodayCards();
    renderLive();
  }catch{}
}

async function loadDaily(){
  const days = document.getElementById('days-select').value;
  document.getElementById('days-select2').value = days;
  try{
    const r = await fetch(`/api/daily?days=${days}`);
    const data = await r.json();
    dailySummary = data.summary || [];
    dailyDetail  = data.detail  || [];
    renderDailyCards();
    renderDailyTable();
    renderDetail();
  }catch{}
}

function fmtSec(s){
  if(!s||s<1) return '0h 0m';
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60);
  return h?(m?`${h}h ${m}m`:`${h}h`):`${m}m`;
}

function renderTodayCards(){
  const total  = allSystems.length;
  const idle   = allSystems.filter(s=>s.status==='idle').length;
  const active = allSystems.filter(s=>s.status==='active').length;
  const hours  = allSystems.reduce((sum,s)=>sum+(s.daily_idle_sec||0),0);
  document.getElementById('c-total').textContent  = total;
  document.getElementById('c-idle').textContent   = idle;
  document.getElementById('c-active').textContent = active;
  document.getElementById('c-hours').textContent  = (hours/3600).toFixed(1)+'h';
  document.getElementById('c-idle-pct').textContent   = total?Math.round(idle/total*100)+'% of systems':'';
  document.getElementById('c-active-pct').textContent = total?Math.round(active/total*100)+'% of systems':'';
}

function renderLive(){
  const q = document.getElementById('search-live').value.trim().toLowerCase();
  let data = allSystems.filter(s=> !q || s.hostname.toLowerCase().includes(q) || (s.ip||'').includes(q));
  data.sort((a,b)=>b.daily_idle_sec-a.daily_idle_sec);
  const tbody = document.getElementById('tbody-live');
  if(!data.length){
    tbody.innerHTML='<tr><td colspan="7" class="empty">No systems reporting yet...</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(s=>`<tr>
    <td class="hn">${s.hostname}</td>
    <td class="ip">${s.ip||'—'}</td>
    <td><span class="pill pill-${s.status}">${s.status}</span></td>
    <td class="${s.status==='idle'?'dur-idle':''}">${s.status==='idle'?(s.current_idle_dur||'—'):'—'}</td>
    <td style="color:var(--muted)">${s.status==='idle'&&s.idle_since?s.idle_since:'—'}</td>
    <td class="dur-day">${s.daily_idle_dur||'0s'}</td>
    <td style="color:var(--muted)">${s.last_seen||'—'}</td>
  </tr>`).join('');
  const ic=data.filter(s=>s.status==='idle').length;
  document.getElementById('foot-live').textContent=`${data.length} systems · ${ic} idle · ${data.length-ic} active`;
}

function renderDailyCards(){
  const container = document.getElementById('day-cards');
  if(!dailySummary.length){
    container.innerHTML='<p style="color:var(--muted);font-size:12px">No history yet.</p>';
    return;
  }
  container.innerHTML = dailySummary.slice(0,7).map(d=>{
    const pct = d.total_systems ? Math.round(d.idle_systems/d.total_systems*100) : 0;
    const isToday = d.report_date === new Date().toISOString().split('T')[0];
    return `<div class="day-card">
      <div class="day-card-head">
        <span class="day-card-date">${isToday?'Today':d.report_date}</span>
        <span class="day-card-hours">${parseFloat(d.total_idle_hours||0).toFixed(1)}h idle</span>
      </div>
      <div class="day-stats">
        <div class="day-stat"><div class="day-stat-val">${d.total_systems||0}</div><div class="day-stat-label">Total</div></div>
        <div class="day-stat"><div class="day-stat-val" style="color:var(--idle)">${d.idle_systems||0}</div><div class="day-stat-label">Idle</div></div>
        <div class="day-stat"><div class="day-stat-val" style="color:var(--active)">${d.active_systems||0}</div><div class="day-stat-label">Active</div></div>
      </div>
      <div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div>
    </div>`;
  }).join('');
}

function renderDailyTable(){
  const tbody = document.getElementById('tbody-daily');
  if(!dailySummary.length){
    tbody.innerHTML='<tr><td colspan="6" class="empty">No history yet.</td></tr>';
    return;
  }
  tbody.innerHTML = dailySummary.map(d=>{
    const pct = d.total_systems ? Math.round(d.idle_systems/d.total_systems*100) : 0;
    const isToday = d.report_date === new Date().toISOString().split('T')[0];
    return `<tr>
      <td><span class="date-badge">${isToday?'Today':d.report_date}</span></td>
      <td>${d.total_systems||0}</td>
      <td style="color:var(--idle);font-weight:500">${d.idle_systems||0}</td>
      <td style="color:var(--active);font-weight:500">${d.active_systems||0}</td>
      <td class="dur-hours">${parseFloat(d.total_idle_hours||0).toFixed(2)}h</td>
      <td>${pct}%</td>
    </tr>`;
  }).join('');
}

function renderDetail(){
  const q = document.getElementById('search-detail').value.trim().toLowerCase();
  let data = dailyDetail.filter(r=> !q || r.hostname.toLowerCase().includes(q));
  const tbody = document.getElementById('tbody-detail');
  if(!data.length){
    tbody.innerHTML='<tr><td colspan="4" class="empty">No history yet.</td></tr>';
    document.getElementById('foot-detail').textContent='';
    return;
  }
  tbody.innerHTML = data.map(r=>`<tr>
    <td><span class="date-badge">${r.report_date}</span></td>
    <td class="hn">${r.hostname}</td>
    <td class="ip">${r.ip||'—'}</td>
    <td class="dur-day">${r.total_idle_dur||'0s'}</td>
  </tr>`).join('');
  document.getElementById('foot-detail').textContent=`${data.length} records`;
}

function switchTab(name,btn){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('on'));
  btn.classList.add('on');
  document.getElementById('tab-'+name).classList.add('on');
}

loadAll();
setInterval(loadAll,60000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("Connecting to PostgreSQL...")
    init_db()
    print("="*45)
    print("  Network Idle Monitor — Server")
    print(f"  DB:        {DB_NAME} @ {DB_HOST}:{DB_PORT}")
    print("  Dashboard: http://0.0.0.0:12001")
    print("="*45)
    app.run(host="0.0.0.0", port=12001, debug=False)
