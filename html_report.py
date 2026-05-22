import json
import datetime
import decimal
import uuid
from pathlib import Path
from html import escape as h

REPORTS_DIR = Path(__file__).parent / "reports"


# ── Serialization ────────────────────────────────────────────────────────────

def _serialize(obj):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return str(obj)


# ── Column metadata detection ─────────────────────────────────────────────────

def _detect_columns(raw_data: list, columns: list) -> dict:
    empty = {
        "categorical": [], "has_date": False, "has_amount": False,
        "is_row_level": False, "date_min": "", "date_max": "",
        "amount_min": 0, "amount_max": 0,
        "date_col": None, "amount_col": None,
        "flag_col": None, "badge_col": None, "id_cols": [],
    }
    if not raw_data:
        return empty

    # ── ID columns ────────────────────────────────────────────────────────────
    id_cols = [
        c for c in columns
        if c.lower().endswith("_id") or c.lower().startswith("id_") or c.lower() == "id"
    ]

    # ── Date column ───────────────────────────────────────────────────────────
    DATE_KW = {"date", "time", "timestamp", "created", "updated", "at", "on", "day", "month", "year"}
    date_col = None
    for c in columns:
        if set(c.lower().replace("_", " ").split()) & DATE_KW:
            date_col = c
            break
    if not date_col:
        for c in columns:
            sample = next((r[c] for r in raw_data if r.get(c) is not None), None)
            if isinstance(sample, (datetime.datetime, datetime.date)):
                date_col = c
                break

    # ── Amount column ─────────────────────────────────────────────────────────
    AMOUNT_KW = {"amount", "price", "value", "cost", "revenue", "total", "fee", "balance", "salary", "pay", "sum"}
    amount_col = None
    for c in columns:
        if set(c.lower().replace("_", " ").split()) & AMOUNT_KW:
            vals = [r[c] for r in raw_data if r.get(c) is not None]
            try:
                [float(v) for v in vals[:20]]
                amount_col = c
                break
            except (ValueError, TypeError):
                pass

    # ── Binary flag column ────────────────────────────────────────────────────
    FLAG_KW = {"flag", "fraud", "churn", "default", "label", "target", "spam", "fake", "anomaly"}
    flag_col = None
    for c in columns:
        if c in id_cols:
            continue
        lower = c.lower()
        kw_match = bool(set(lower.replace("_", " ").split()) & FLAG_KW) or lower.startswith(("is_", "has_"))
        if kw_match:
            vals = {str(r[c]) for r in raw_data if r.get(c) is not None}
            if vals <= {"0", "1", "True", "False", "true", "false"}:
                flag_col = c
                break
    if not flag_col:
        for c in columns:
            if c in id_cols or c == amount_col or c == date_col:
                continue
            vals = {str(r[c]) for r in raw_data if r.get(c) is not None}
            if vals <= {"0", "1"}:
                flag_col = c
                break

    # ── Badge/risk column ─────────────────────────────────────────────────────
    BADGE_KW = {"risk", "level", "grade", "tier", "priority", "severity", "status", "class", "category"}
    badge_col = None
    for c in columns:
        if c == flag_col or c in id_cols:
            continue
        if set(c.lower().replace("_", " ").split()) & BADGE_KW:
            vals = {str(r[c]) for r in raw_data if r.get(c) is not None}
            if 1 < len(vals) <= 10:
                badge_col = c
                break

    # ── Categorical columns ───────────────────────────────────────────────────
    skip = set(id_cols) | {date_col, amount_col, flag_col}
    categorical = []
    for c in columns:
        if c in skip or c is None:
            continue
        vals   = [str(r[c]) for r in raw_data if r.get(c) is not None]
        unique = sorted(set(vals))
        if 1 < len(unique) <= 30:
            categorical.append({"col": c, "values": unique})

    is_row_level = len(raw_data) > 10 and bool(id_cols or date_col)

    result = {
        "categorical":  categorical,
        "has_date":     bool(date_col),
        "has_amount":   bool(amount_col),
        "is_row_level": is_row_level,
        "date_min": "", "date_max": "",
        "amount_min": 0, "amount_max": 0,
        "date_col":   date_col,
        "amount_col": amount_col,
        "flag_col":   flag_col,
        "badge_col":  badge_col,
        "id_cols":    id_cols,
    }

    if date_col:
        dates = sorted(
            str(r[date_col])[:10]
            for r in raw_data if r.get(date_col)
        )
        if dates:
            result["date_min"] = dates[0]
            result["date_max"] = dates[-1]

    if amount_col:
        amounts = []
        for r in raw_data:
            v = r.get(amount_col)
            if v is not None:
                try:
                    amounts.append(float(v))
                except (ValueError, TypeError):
                    pass
        if amounts:
            result["amount_min"] = round(min(amounts), 2)
            result["amount_max"] = round(max(amounts), 2)

    return result


# ── Filter pane HTML ──────────────────────────────────────────────────────────

def _build_filter_controls(col_meta: dict) -> str:
    if not col_meta["is_row_level"]:
        return '<div class="no-filter-msg">Aggregated view — slicers not applicable</div>'

    parts = []
    for item in col_meta["categorical"]:
        col   = item["col"]
        label = col.replace("_", " ").title()
        opts  = "\n".join(f'<option value="{h(v)}">{h(v)}</option>' for v in item["values"])
        parts.append(f"""
<div class="filter-group">
  <label class="filter-label">
    {h(label)}
    <span class="filter-badge" id="badge-{col}" style="display:none">1</span>
  </label>
  <select class="filter-select" id="filter-{col}"
          onchange="onSlicerChange('{col}', this.value)">
    <option value="">All</option>
    {opts}
  </select>
</div>""")

    if col_meta["has_date"]:
        dmin = h(col_meta["date_min"])
        dmax = h(col_meta["date_max"])
        parts.append(f"""
<div class="filter-group">
  <label class="filter-label">Date Range</label>
  <input type="date" class="filter-input" id="filter-date-from"
         value="{dmin}" min="{dmin}" max="{dmax}" onchange="onDateChange()">
  <span class="date-sep">→</span>
  <input type="date" class="filter-input" id="filter-date-to"
         value="{dmax}" min="{dmin}" max="{dmax}" onchange="onDateChange()">
</div>""")

    if col_meta["has_amount"]:
        amin = col_meta["amount_min"]
        amax = col_meta["amount_max"]
        amt_label = (col_meta.get("amount_col") or "amount").replace("_", " ").title()
        parts.append(f"""
<div class="filter-group">
  <label class="filter-label">{h(amt_label)}</label>
  <div class="amount-row">
    <input type="number" class="filter-input half" id="filter-amt-min"
           placeholder="Min" value="{amin:.0f}" onchange="onAmountChange()">
    <input type="number" class="filter-input half" id="filter-amt-max"
           placeholder="Max" value="{amax:.0f}" onchange="onAmountChange()">
  </div>
</div>""")

    return "\n".join(parts)


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #201f1e;
  --canvas:   #252423;
  --card:     #2d2c2b;
  --border:   #3b3a39;
  --accent:   #118dff;
  --green:    #00b294;
  --red:      #fd625e;
  --yellow:   #f2c811;
  --purple:   #8967e0;
  --text:     #f3f2f1;
  --muted:    #a19f9d;
  --pane-w:   260px;
}

html, body { height: 100%; overflow: hidden; }

body {
  font-family: 'Inter', sans-serif;
  background: var(--bg);
  color: var(--text);
  display: flex;
  flex-direction: column;
}

/* ── Header ── */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: .6rem 1.2rem;
  background: #1a1918;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 1rem;
}
.header-left h1 {
  font-size: 1rem;
  font-weight: 700;
  color: #fff;
  letter-spacing: .02em;
}
.header-left .meta {
  font-size: .72rem;
  color: var(--muted);
  margin-top: 2px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: .75rem;
  flex: 1;
  justify-content: flex-end;
}
.question-badge {
  background: rgba(17,141,255,.12);
  border: 1px solid rgba(17,141,255,.25);
  border-radius: 20px;
  padding: .3rem .9rem;
  font-size: .78rem;
  color: #7fc4ff;
  max-width: 520px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.btn-reset {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--muted);
  border-radius: 6px;
  padding: .3rem .85rem;
  font-size: .78rem;
  cursor: pointer;
  white-space: nowrap;
  transition: all .15s;
}
.btn-reset:hover { border-color: var(--accent); color: var(--accent); }

/* ── Layout ── */
.main { display: flex; flex: 1; overflow: hidden; }

/* ── Filter Pane ── */
.filter-pane {
  width: var(--pane-w);
  flex-shrink: 0;
  background: #1a1918;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: .75rem .65rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.filter-pane::-webkit-scrollbar { width: 4px; }
.filter-pane::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

.pane-title {
  font-size: .65rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .12em;
  color: var(--muted);
  padding: .4rem .25rem .6rem;
  border-bottom: 1px solid var(--border);
  margin-bottom: .5rem;
}

.filter-group { margin-bottom: .85rem; }

.filter-label {
  display: flex;
  align-items: center;
  gap: .4rem;
  font-size: .72rem;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .06em;
  margin-bottom: .3rem;
}

.filter-badge {
  background: var(--accent);
  color: #fff;
  font-size: .6rem;
  font-weight: 700;
  border-radius: 10px;
  padding: .05rem .35rem;
  line-height: 1.4;
}

.filter-select, .filter-input {
  width: 100%;
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 5px;
  padding: .35rem .5rem;
  font-size: .78rem;
  font-family: inherit;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
}
.filter-select {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23a19f9d'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right .5rem center;
  padding-right: 1.5rem;
}
.filter-select:focus, .filter-input:focus { border-color: var(--accent); }

.date-sep {
  display: block;
  text-align: center;
  color: var(--muted);
  font-size: .7rem;
  margin: .2rem 0;
}

.amount-row { display: flex; gap: .4rem; }
.filter-input.half { width: 50%; }

.no-filter-msg {
  font-size: .75rem;
  color: var(--muted);
  font-style: italic;
  padding: .5rem .25rem;
}

/* ── Canvas ── */
.canvas {
  flex: 1;
  overflow-y: auto;
  background: var(--canvas);
  padding: .85rem;
  display: flex;
  flex-direction: column;
  gap: .75rem;
}
.canvas::-webkit-scrollbar { width: 5px; }
.canvas::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

/* ── KPI Strip ── */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: .65rem;
}
.kpi-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .85rem 1rem;
  border-top: 3px solid var(--accent);
}
.kpi-value {
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1.1;
}
.kpi-label {
  font-size: .72rem;
  color: var(--muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-top: .3rem;
}
.kpi-sub {
  font-size: .68rem;
  color: var(--muted);
  margin-top: .2rem;
}

/* ── Chart Grid ── */
.chart-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: .75rem;
}
.chart-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .75rem 1rem .5rem;
}
.chart-card-full {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .75rem 1rem .5rem;
}
.chart-title {
  font-size: .75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: var(--muted);
  margin-bottom: .5rem;
}
.chart-hint {
  font-size: .65rem;
  color: var(--muted);
  margin-top: .3rem;
  font-style: italic;
}

/* ── Insights Section ── */
.section-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .85rem 1rem;
}
.section-header {
  display: flex;
  align-items: center;
  gap: .6rem;
  margin-bottom: .75rem;
  font-size: .75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: var(--muted);
}
.badge-ai {
  background: rgba(137,103,224,.18);
  color: var(--purple);
  border: 1px solid rgba(137,103,224,.3);
  border-radius: 4px;
  padding: .1rem .4rem;
  font-size: .65rem;
  font-weight: 700;
  text-transform: uppercase;
}

/* Spinner */
.spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin .7s linear infinite;
  display: inline-block;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.insight-summary {
  font-size: .85rem;
  color: var(--text);
  line-height: 1.6;
  margin-bottom: .75rem;
  padding: .6rem .85rem;
  background: rgba(17,141,255,.07);
  border-left: 3px solid var(--accent);
  border-radius: 0 6px 6px 0;
}

.insight-kpi-row {
  display: flex;
  gap: .65rem;
  flex-wrap: wrap;
  margin-bottom: .75rem;
}
.insight-kpi {
  background: rgba(255,255,255,.04);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: .5rem .75rem;
  min-width: 130px;
}
.ik-value { font-size: 1.1rem; font-weight: 700; color: var(--accent); }
.ik-name  { font-size: .7rem; color: var(--text); font-weight: 600; margin-top: .15rem; }
.ik-interp { font-size: .67rem; color: var(--muted); margin-top: .1rem; }

.insight-section { margin-bottom: .6rem; }
.insight-section-title {
  font-size: .7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--green);
  margin-bottom: .35rem;
}
.anomaly-title { color: var(--yellow); }

.insight-list { padding-left: 1.1rem; }
.insight-list li { font-size: .82rem; color: var(--text); margin-bottom: .3rem; line-height: 1.5; }
.insight-list li::marker { color: var(--green); }

.anomaly-list { padding-left: 1.1rem; }
.anomaly-list li { font-size: .82rem; color: var(--text); margin-bottom: .3rem; line-height: 1.5; }
.anomaly-list li::marker { color: var(--yellow); }

.recommendation {
  background: rgba(0,178,148,.07);
  border: 1px solid rgba(0,178,148,.2);
  border-radius: 6px;
  padding: .6rem .85rem;
  font-size: .82rem;
  color: var(--green);
  margin-top: .65rem;
  line-height: 1.5;
}

/* ── Causal Attribution ── */
.causal-section { margin-top: .75rem; }
.causal-title {
  font-size: .7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--purple);
  margin-bottom: .45rem;
}
.causal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: .45rem;
}
.causal-card {
  background: rgba(137,103,224,.07);
  border: 1px solid rgba(137,103,224,.2);
  border-radius: 6px;
  padding: .55rem .75rem;
  font-size: .78rem;
}
.causal-card.confounder {
  background: rgba(242,200,17,.05);
  border-color: rgba(242,200,17,.18);
}
.causal-factor { font-weight: 600; color: var(--text); margin-bottom: .18rem; }
.causal-tag {
  display: inline-block;
  font-size: .62rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  padding: .1rem .35rem;
  border-radius: 3px;
  margin-bottom: .3rem;
  background: rgba(137,103,224,.25);
  color: var(--purple);
}
.causal-card.confounder .causal-tag {
  background: rgba(242,200,17,.2);
  color: var(--yellow);
}
.causal-effect { color: var(--accent); font-weight: 600; font-size: .8rem; }
.causal-note { color: var(--muted); font-size: .72rem; margin-top: .18rem; line-height: 1.4; }

/* ── Data Table ── */
.table-count { font-size: .72rem; color: var(--muted); }

.table-wrapper { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .78rem; }
thead th {
  background: rgba(17,141,255,.1);
  color: var(--accent);
  font-weight: 600;
  padding: .4rem .65rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
}
thead th:hover { background: rgba(17,141,255,.17); }
tbody td {
  padding: .38rem .65rem;
  border-bottom: 1px solid rgba(255,255,255,.04);
  color: var(--text);
}
tbody tr:hover td { background: rgba(255,255,255,.03); }

.sort-icon { opacity: .45; font-size: .7rem; }

/* Status badges */
.badge-fraud {
  background: rgba(253,98,94,.15);
  color: var(--red);
  border: 1px solid rgba(253,98,94,.3);
  border-radius: 4px; padding: .1rem .4rem; font-size: .7rem; font-weight: 600;
}
.badge-legit {
  background: rgba(0,178,148,.12);
  color: var(--green);
  border: 1px solid rgba(0,178,148,.25);
  border-radius: 4px; padding: .1rem .4rem; font-size: .7rem; font-weight: 600;
}
.badge-high   { background: rgba(253,98,94,.15);  color: var(--red);    border: 1px solid rgba(253,98,94,.3);  border-radius: 4px; padding: .1rem .4rem; font-size: .7rem; font-weight: 600; }
.badge-medium { background: rgba(242,200,17,.12); color: var(--yellow); border: 1px solid rgba(242,200,17,.25); border-radius: 4px; padding: .1rem .4rem; font-size: .7rem; font-weight: 600; }
.badge-low    { background: rgba(0,178,148,.12);  color: var(--green);  border: 1px solid rgba(0,178,148,.25); border-radius: 4px; padding: .1rem .4rem; font-size: .7rem; font-weight: 600; }

/* ── Pagination ── */
.pagination {
  display: flex;
  align-items: center;
  gap: .35rem;
  margin-top: .65rem;
  flex-wrap: wrap;
}
.pagination button {
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 5px;
  padding: .25rem .55rem;
  font-size: .75rem;
  cursor: pointer;
  font-family: inherit;
  transition: all .12s;
}
.pagination button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pagination button:disabled { opacity: .35; cursor: default; }
.pagination button.active-page { background: var(--accent); border-color: var(--accent); color: #fff; }
.pg-ellipsis { color: var(--muted); font-size: .75rem; padding: 0 .2rem; }

/* ── SQL ── */
.sql-pre {
  background: #0d0f18;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: .85rem 1rem;
  overflow-x: auto;
  font-size: .78rem;
  color: #a5b4fc;
  line-height: 1.7;
  font-family: 'Consolas', 'Fira Code', monospace;
}
"""


# ── JavaScript ────────────────────────────────────────────────────────────────

_JS_TEMPLATE = r"""
// ── Embedded data ──────────────────────────────────────────────────────────
const RAW_DATA   = __DATA_JSON__;
const COLUMNS    = __COLS_JSON__;
const COL_META   = __META_JSON__;
const QUESTION   = __QUESTION_JSON__;
let   ANALYTICS  = __ANALYTICS_JSON__;

// ── State ──────────────────────────────────────────────────────────────────
let slicerFilters  = {};
let crossFilters   = {};
let dateFrom       = COL_META.date_min  || null;
let dateTo         = COL_META.date_max  || null;
let amtMin         = COL_META.has_amount ? COL_META.amount_min : null;
let amtMax         = COL_META.has_amount ? COL_META.amount_max : null;
let currentPage    = 0;
const PAGE_SIZE    = 50;
let sortCol        = null;
let sortDir        = 1;
let filteredRows   = RAW_DATA.slice();
let reanalyzeTimer = null;
const EC           = {};  // ECharts instances

const PALETTE = ['#118dff','#fd625e','#f2c811','#00b294','#8967e0','#ff9f43','#4ecdc4','#ee5a24'];

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  buildTableHeader();
  applyFilters();
  window.addEventListener('resize', () => {
    Object.values(EC).forEach(c => { try { c.resize(); } catch(_){} });
  });
  updateInsights(ANALYTICS);
});

// ── Filter engine ──────────────────────────────────────────────────────────
function applyFilters() {
  let rows = RAW_DATA;

  for (const [col, val] of Object.entries(slicerFilters)) {
    if (val !== '' && val !== null)
      rows = rows.filter(r => String(r[col] ?? '') === val);
  }
  for (const [col, val] of Object.entries(crossFilters)) {
    rows = rows.filter(r => String(r[col] ?? '') === val);
  }
  if (dateFrom && COL_META.date_col) rows = rows.filter(r =>
    String(r[COL_META.date_col] || '').slice(0, 10) >= dateFrom);
  if (dateTo && COL_META.date_col) rows = rows.filter(r =>
    String(r[COL_META.date_col] || '').slice(0, 10) <= dateTo);
  if (amtMin !== null && amtMin !== '' && COL_META.amount_col)
    rows = rows.filter(r => parseFloat(r[COL_META.amount_col] ?? 0) >= parseFloat(amtMin));
  if (amtMax !== null && amtMax !== '' && COL_META.amount_col)
    rows = rows.filter(r => parseFloat(r[COL_META.amount_col] ?? 0) <= parseFloat(amtMax));

  filteredRows = rows;
  updateKPIs(rows);
  renderBarChart(rows);
  renderDonutChart(rows);
  if (COL_META.has_date) renderLineChart(rows);
  currentPage = 0;
  renderTable(rows);
  updateFilterBadges();
  if (COL_META.is_row_level) triggerReanalyze(rows);
}

// ── Slicer events ──────────────────────────────────────────────────────────
function onSlicerChange(col, val) { slicerFilters[col] = val; applyFilters(); }

function onDateChange() {
  dateFrom = document.getElementById('filter-date-from')?.value || null;
  dateTo   = document.getElementById('filter-date-to')?.value   || null;
  applyFilters();
}

function onAmountChange() {
  amtMin = document.getElementById('filter-amt-min')?.value;
  amtMax = document.getElementById('filter-amt-max')?.value;
  applyFilters();
}

function resetAll() {
  slicerFilters = {};
  crossFilters  = {};
  document.querySelectorAll('.filter-select').forEach(s => s.value = '');
  const df = document.getElementById('filter-date-from');
  const dt = document.getElementById('filter-date-to');
  if (df) { df.value = COL_META.date_min || ''; dateFrom = COL_META.date_min || null; }
  if (dt) { dt.value = COL_META.date_max || ''; dateTo   = COL_META.date_max || null; }
  const mn = document.getElementById('filter-amt-min');
  const mx = document.getElementById('filter-amt-max');
  if (mn) { mn.value = COL_META.amount_min ?? ''; amtMin = COL_META.amount_min ?? null; }
  if (mx) { mx.value = COL_META.amount_max ?? ''; amtMax = COL_META.amount_max ?? null; }
  applyFilters();
}

// ── KPIs ───────────────────────────────────────────────────────────────────
function updateKPIs(rows) {
  const strip = document.getElementById('kpi-strip');
  if (!strip) return;

  const flagCol   = COL_META.flag_col;
  const amtCol    = COL_META.amount_col;
  const idCol     = COL_META.id_cols && COL_META.id_cols.length > 0 ? COL_META.id_cols[0] : null;
  const hasFraud  = flagCol  && COLUMNS.includes(flagCol);
  const hasAmount = amtCol   && COLUMNS.includes(amtCol);
  const hasId     = idCol    && COLUMNS.includes(idCol);
  const total     = rows.length;

  const cards = [];
  cards.push({ label: 'Rows', value: total.toLocaleString(), color: '#118dff', top: '#118dff' });

  if (hasFraud && total > 0) {
    const fraudN   = rows.filter(r => r[flagCol] == 1 || r[flagCol] === true || r[flagCol] === 'True').length;
    const rate     = (fraudN / total * 100).toFixed(1);
    const color    = rate > 50 ? '#fd625e' : rate > 30 ? '#f2c811' : '#00b294';
    const flagLabel = flagCol.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
    cards.push({ label: flagLabel + ' Rate', value: rate + '%', sub: fraudN.toLocaleString() + ' flagged', color, top: color });
  }

  if (hasAmount && total > 0) {
    const amts   = rows.map(r => parseFloat(r[amtCol] ?? 0));
    const avg    = amts.reduce((a, b) => a + b, 0) / amts.length;
    const sum    = amts.reduce((a, b) => a + b, 0);
    const amtLabel = amtCol.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
    cards.push({ label: 'Avg ' + amtLabel, value: avg.toFixed(2), sub: 'Total ' + sum.toFixed(0), color: '#118dff', top: '#118dff' });
  }

  if (hasId && total > 0) {
    const uniq     = new Set(rows.map(r => r[idCol])).size;
    const idLabel  = idCol.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
    cards.push({ label: 'Unique ' + idLabel, value: uniq.toLocaleString(), color: '#8967e0', top: '#8967e0' });
  }

  strip.innerHTML = cards.map(k => `
    <div class="kpi-card" style="border-top-color:${k.top}">
      <div class="kpi-value" style="color:${k.color}">${esc(k.value)}</div>
      <div class="kpi-label">${esc(k.label)}</div>
      ${k.sub ? `<div class="kpi-sub">${esc(k.sub)}</div>` : ''}
    </div>`).join('');
}

// ── Aggregation ────────────────────────────────────────────────────────────
function groupBy(rows, col) {
  const m        = {};
  const flagCol  = COL_META.flag_col;
  const amtCol   = COL_META.amount_col;
  for (const r of rows) {
    const k = String(r[col] ?? 'Unknown');
    if (!m[k]) m[k] = { total: 0, fraud: 0, amount: 0 };
    m[k].total++;
    if (flagCol && (r[flagCol] == 1 || r[flagCol] === true || r[flagCol] === 'True')) m[k].fraud++;
    m[k].amount += amtCol ? parseFloat(r[amtCol] ?? 0) : 0;
  }
  return m;
}

// ── ECharts helpers ────────────────────────────────────────────────────────
function getChart(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  if (EC[id]) { EC[id].clear(); return EC[id]; }
  EC[id] = echarts.init(el, 'dark', { renderer: 'canvas' });
  return EC[id];
}

// ── Bar chart ──────────────────────────────────────────────────────────────
function renderBarChart(rows) {
  const chart = getChart('chart-bar');
  if (!chart || !COL_META.categorical.length) return;

  const col      = COL_META.categorical[0].col;
  const hasFraud = COL_META.flag_col && COLUMNS.includes(COL_META.flag_col);
  const label    = col.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
  const titleEl  = document.getElementById('chart-bar-title');
  if (titleEl) titleEl.textContent = hasFraud ? `Fraud Rate by ${label}` : `Count by ${label}`;

  const groups   = groupBy(rows, col);
  const cats     = Object.keys(groups).sort();
  const selVal   = crossFilters[col];

  const values = cats.map(k => {
    const g   = groups[k];
    const val = hasFraud && g.total > 0 ? +(g.fraud / g.total * 100).toFixed(1) : g.total;
    const dim = selVal && k !== selVal;
    return {
      value: val,
      itemStyle: {
        color: dim ? 'rgba(17,141,255,.18)' : (hasFraud ? '#fd625e' : '#118dff'),
        borderRadius: [4, 4, 0, 0],
      },
    };
  });

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1918',
      borderColor: '#3b3a39',
      textStyle: { color: '#f3f2f1', fontSize: 12 },
      formatter: p => `${p[0].name}: <b>${p[0].value}${hasFraud ? '%' : ''}</b>`,
    },
    grid: { left: 8, right: 8, bottom: cats.length > 4 ? 55 : 30, top: 10, containLabel: true },
    xAxis: {
      type: 'category', data: cats,
      axisLabel: { color: '#a19f9d', fontSize: 11, rotate: cats.length > 4 ? 28 : 0 },
      axisLine: { lineStyle: { color: '#3b3a39' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#a19f9d', formatter: hasFraud ? '{value}%' : '{value}' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,.06)' } },
    },
    series: [{ type: 'bar', data: values, emphasis: { itemStyle: { color: '#f2c811' } } }],
  }, true);

  chart.off('click');
  chart.on('click', params => {
    if (crossFilters[col] === params.name) delete crossFilters[col];
    else crossFilters[col] = params.name;
    applyFilters();
  });
}

// ── Donut chart ────────────────────────────────────────────────────────────
function renderDonutChart(rows) {
  const chart = getChart('chart-donut');
  if (!chart || !COL_META.categorical.length) return;

  const item   = COL_META.categorical.length > 1 ? COL_META.categorical[1] : COL_META.categorical[0];
  const col    = item.col;
  const label  = col.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
  const titleEl = document.getElementById('chart-donut-title');
  if (titleEl) titleEl.textContent = `Distribution — ${label}`;

  const groups  = groupBy(rows, col);
  const selVal  = crossFilters[col];

  const data = Object.entries(groups)
    .sort((a, b) => b[1].total - a[1].total)
    .map(([k, v], i) => ({
      name: k, value: v.total,
      itemStyle: {
        color: PALETTE[i % PALETTE.length],
        opacity: selVal && k !== selVal ? 0.2 : 1,
      },
    }));

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#1a1918',
      borderColor: '#3b3a39',
      textStyle: { color: '#f3f2f1', fontSize: 12 },
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      orient: 'vertical', right: 4, top: 'center',
      textStyle: { color: '#a19f9d', fontSize: 11 },
      itemWidth: 10, itemHeight: 10,
    },
    series: [{
      type: 'pie',
      radius: ['38%', '68%'],
      center: ['38%', '50%'],
      data,
      emphasis: { itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0,0,0,.4)' } },
      label: { show: false },
      labelLine: { show: false },
    }],
  }, true);

  chart.off('click');
  chart.on('click', params => {
    if (crossFilters[col] === params.name) delete crossFilters[col];
    else crossFilters[col] = params.name;
    applyFilters();
  });
}

// ── Line chart ─────────────────────────────────────────────────────────────
function renderLineChart(rows) {
  const chart = getChart('chart-line');
  if (!chart) return;

  const byDay = {}, byDayFraud = {};
  const dateCol = COL_META.date_col;
  const flagCol = COL_META.flag_col;
  for (const r of rows) {
    const d = String(r[dateCol] || '').slice(0, 10);
    if (!d || d === 'null') continue;
    byDay[d] = (byDay[d] || 0) + 1;
    if (flagCol && (r[flagCol] == 1 || r[flagCol] === true || r[flagCol] === 'True'))
      byDayFraud[d] = (byDayFraud[d] || 0) + 1;
  }

  const dates    = Object.keys(byDay).sort();
  const hasFraud = flagCol && COLUMNS.includes(flagCol);

  const series = [{
    name: 'Total', type: 'line',
    data: dates.map(d => byDay[d]),
    smooth: true,
    lineStyle: { color: '#118dff', width: 2 },
    areaStyle: { color: 'rgba(17,141,255,.08)' },
    symbol: 'none',
  }];

  if (hasFraud) {
    series.push({
      name: 'Fraud', type: 'line',
      data: dates.map(d => byDayFraud[d] || 0),
      smooth: true,
      lineStyle: { color: '#fd625e', width: 2 },
      areaStyle: { color: 'rgba(253,98,94,.08)' },
      symbol: 'none',
    });
  }

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1918',
      borderColor: '#3b3a39',
      textStyle: { color: '#f3f2f1', fontSize: 12 },
    },
    legend: { textStyle: { color: '#a19f9d', fontSize: 11 }, top: 0 },
    grid: { left: 8, right: 8, bottom: 25, top: 30, containLabel: true },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { color: '#a19f9d', fontSize: 10, formatter: v => v.slice(5) },
      axisLine: { lineStyle: { color: '#3b3a39' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#a19f9d' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,.06)' } },
    },
    series,
  }, true);
}

// ── Table ──────────────────────────────────────────────────────────────────
function buildTableHeader() {
  const thead = document.getElementById('table-head');
  if (!thead) return;
  const ths = COLUMNS.map(col => {
    const label = col.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
    return `<th onclick="onSort('${col}')" data-col="${col}">${esc(label)} <span class="sort-icon">↕</span></th>`;
  }).join('');
  thead.innerHTML = `<tr>${ths}</tr>`;
}

function renderTable(rows) {
  const tbody = document.getElementById('table-body');
  const count = document.getElementById('table-count');
  if (!tbody) return;

  let sorted = [...rows];
  if (sortCol) {
    sorted.sort((a, b) => {
      const va = a[sortCol] ?? '', vb = b[sortCol] ?? '';
      if (va < vb) return -sortDir;
      if (va > vb) return  sortDir;
      return 0;
    });
  }

  const start    = currentPage * PAGE_SIZE;
  const pageRows = sorted.slice(start, start + PAGE_SIZE);

  tbody.innerHTML = pageRows.map(row => {
    const cells = COLUMNS.map(col => {
      let val = row[col] ?? '';
      if (COL_META.flag_col && col === COL_META.flag_col) {
        const isFlagged = val == 1 || val === true || val === 'True' || val === 'true';
        val = isFlagged ? '<span class="badge-fraud">Yes</span>' : '<span class="badge-legit">No</span>';
      } else if (COL_META.badge_col && col === COL_META.badge_col) {
        const cls = {High:'badge-high', Medium:'badge-medium', Low:'badge-low'}[val] || '';
        val = cls ? `<span class="${cls}">${esc(String(val))}</span>` : esc(String(val).slice(0, 48));
      } else {
        val = esc(String(val).slice(0, 48));
      }
      return `<td>${val}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  if (count) {
    const end = Math.min(start + PAGE_SIZE, rows.length);
    count.textContent = rows.length === 0
      ? '0 rows'
      : `${start + 1}–${end} of ${rows.length.toLocaleString()} rows`;
  }

  renderPagination(rows.length);
}

function renderPagination(total) {
  const pg = document.getElementById('pagination');
  if (!pg) return;
  const totalPages = Math.ceil(total / PAGE_SIZE);
  if (totalPages <= 1) { pg.innerHTML = ''; return; }

  let html = `<button onclick="goPage(${currentPage-1})" ${currentPage===0?'disabled':''}>← Prev</button>`;
  for (let i = 0; i < totalPages; i++) {
    if (i === 0 || i === totalPages-1 || Math.abs(i - currentPage) <= 2) {
      html += `<button onclick="goPage(${i})" class="${i===currentPage?'active-page':''}">${i+1}</button>`;
    } else if (Math.abs(i - currentPage) === 3) {
      html += `<span class="pg-ellipsis">…</span>`;
    }
  }
  html += `<button onclick="goPage(${currentPage+1})" ${currentPage>=totalPages-1?'disabled':''}>Next →</button>`;
  pg.innerHTML = html;
}

function goPage(p) {
  const tp = Math.ceil(filteredRows.length / PAGE_SIZE);
  if (p < 0 || p >= tp) return;
  currentPage = p;
  renderTable(filteredRows);
}

function onSort(col) {
  sortDir = (sortCol === col) ? -sortDir : 1;
  sortCol = col;
  document.querySelectorAll('[data-col]').forEach(th => {
    const icon = th.querySelector('.sort-icon');
    if (!icon) return;
    icon.textContent = th.dataset.col === sortCol ? (sortDir===1 ? '↑' : '↓') : '↕';
  });
  renderTable(filteredRows);
}

// ── Insights ───────────────────────────────────────────────────────────────
function updateInsights(analytics) {
  const content = document.getElementById('insights-content');
  const spinner = document.getElementById('insights-spinner');
  if (!content) return;
  if (spinner) spinner.style.display = 'none';

  const { summary='', kpis=[], insights=[], anomalies=[], recommendation='', causal_attribution=[] } = analytics;
  let html = '';

  if (summary)
    html += `<div class="insight-summary">${esc(summary)}</div>`;

  if (kpis.length)
    html += `<div class="insight-kpi-row">${kpis.map(k => `
      <div class="insight-kpi">
        <div class="ik-value">${esc(String(k.value))}</div>
        <div class="ik-name">${esc(k.name)}</div>
        <div class="ik-interp">${esc(k.interpretation||'')}</div>
      </div>`).join('')}</div>`;

  if (insights.length)
    html += `<div class="insight-section">
      <div class="insight-section-title">Key Observations</div>
      <ul class="insight-list">${insights.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>
    </div>`;

  if (anomalies.length)
    html += `<div class="insight-section">
      <div class="insight-section-title anomaly-title">Anomalies</div>
      <ul class="anomaly-list">${anomalies.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>
    </div>`;

  if (causal_attribution.length) {
    const cards = causal_attribution.map(c => {
      const cls = c.type === 'confounder' ? 'causal-card confounder' : 'causal-card';
      const tag = c.type === 'confounder' ? 'Confounder' : 'Causal Driver';
      return `<div class="${cls}">
        <div class="causal-factor">${esc(c.factor||'')}</div>
        <span class="causal-tag">${esc(tag)}</span>
        <div class="causal-effect">${esc(c.effect||'')}</div>
        <div class="causal-note">${esc(c.note||'')}</div>
      </div>`;
    }).join('');
    html += `<div class="causal-section">
      <div class="causal-title">Causal Attribution</div>
      <div class="causal-grid">${cards}</div>
    </div>`;
  }

  if (recommendation)
    html += `<div class="recommendation">${esc(recommendation)}</div>`;

  content.innerHTML = html || '<p style="color:#a19f9d;font-size:.82rem">No insights available.</p>';
}

function showInsightsLoading() {
  const spinner = document.getElementById('insights-spinner');
  if (spinner) spinner.style.display = 'inline-block';
}

function triggerReanalyze(rows) {
  clearTimeout(reanalyzeTimer);
  showInsightsLoading();
  reanalyzeTimer = setTimeout(async () => {
    try {
      const resp = await fetch('/api/reanalyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows, columns: COLUMNS, question: QUESTION }),
      });
      if (!resp.ok) throw new Error();
      ANALYTICS = await resp.json();
      updateInsights(ANALYTICS);
    } catch (_) {
      const sp = document.getElementById('insights-spinner');
      if (sp) sp.style.display = 'none';
    }
  }, 800);
}

// ── Filter badges ──────────────────────────────────────────────────────────
function updateFilterBadges() {
  for (const item of COL_META.categorical) {
    const col   = item.col;
    const badge = document.getElementById('badge-' + col);
    if (!badge) continue;
    const active = (slicerFilters[col] && slicerFilters[col] !== '') || crossFilters[col];
    badge.style.display = active ? 'inline' : 'none';
  }
}

// ── Escape helper ──────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
"""


# ── Report builder ────────────────────────────────────────────────────────────

def generate_report(
    question: str,
    sql_query: str,
    raw_data: list,
    columns: list,
    analytics: dict,
) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp    = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename     = f"report_{timestamp}.html"
    filepath     = REPORTS_DIR / filename
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    col_meta = _detect_columns(raw_data, columns)

    data_json      = json.dumps(raw_data,       default=_serialize, ensure_ascii=False)
    cols_json      = json.dumps(columns,         ensure_ascii=False)
    meta_json      = json.dumps(col_meta,        default=_serialize, ensure_ascii=False)
    analytics_json = json.dumps(analytics or {}, ensure_ascii=False)
    question_json  = json.dumps(question,        ensure_ascii=False)

    filter_html  = _build_filter_controls(col_meta)
    line_display = "block" if col_meta["has_date"] else "none"

    js = (_JS_TEMPLATE
          .replace("__DATA_JSON__",     data_json)
          .replace("__COLS_JSON__",     cols_json)
          .replace("__META_JSON__",     meta_json)
          .replace("__ANALYTICS_JSON__", analytics_json)
          .replace("__QUESTION_JSON__", question_json))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Analysis Report — {h(generated_at)}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>{_CSS}</style>
</head>
<body>

<!-- ── Header ── -->
<div class="header">
  <div class="header-left">
    <h1>Data Analysis Report</h1>
    <div class="meta">Generated {h(generated_at)} &nbsp;·&nbsp; {len(raw_data):,} rows fetched</div>
  </div>
  <div class="header-right">
    <div class="question-badge" title="{h(question)}">{h(question)}</div>
    <button class="btn-reset" onclick="resetAll()">Reset Filters</button>
  </div>
</div>

<!-- ── Main ── -->
<div class="main">

  <!-- Filter Pane -->
  <aside class="filter-pane">
    <div class="pane-title">Filters</div>
    {filter_html}
  </aside>

  <!-- Canvas -->
  <main class="canvas">

    <!-- KPI Strip -->
    <div class="kpi-strip" id="kpi-strip"></div>

    <!-- Charts row 1 -->
    <div class="chart-grid">
      <div class="chart-card">
        <div class="chart-title" id="chart-bar-title">Chart</div>
        <div id="chart-bar" style="height:260px"></div>
        <div class="chart-hint">Click a bar to cross-filter</div>
      </div>
      <div class="chart-card">
        <div class="chart-title" id="chart-donut-title">Distribution</div>
        <div id="chart-donut" style="height:260px"></div>
        <div class="chart-hint">Click a segment to cross-filter</div>
      </div>
    </div>

    <!-- Line chart (if timestamp present) -->
    <div class="chart-card-full" style="display:{line_display}">
      <div class="chart-title">Transactions Over Time</div>
      <div id="chart-line" style="height:200px"></div>
    </div>

    <!-- AI Insights -->
    <div class="section-card">
      <div class="section-header">
        <span>AI Analysis</span>
        <span class="badge-ai">Groq LLM</span>
        <div class="spinner" id="insights-spinner" style="display:none"></div>
      </div>
      <div id="insights-content"></div>
    </div>

    <!-- Data Table -->
    <div class="section-card">
      <div class="section-header">
        <span>Data</span>
        <span class="table-count" id="table-count"></span>
      </div>
      <div class="table-wrapper">
        <table>
          <thead id="table-head"></thead>
          <tbody id="table-body"></tbody>
        </table>
      </div>
      <div class="pagination" id="pagination"></div>
    </div>

    <!-- SQL -->
    <div class="section-card">
      <div class="section-header">SQL Query</div>
      <pre class="sql-pre">{h(sql_query)}</pre>
    </div>

  </main>
</div>

<script>
{js}
</script>
</body>
</html>"""

    filepath.write_text(html, encoding="utf-8")
    return str(filepath)
