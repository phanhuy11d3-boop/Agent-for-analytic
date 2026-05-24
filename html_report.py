import datetime
import decimal
import json
import uuid
from html import escape as h
from pathlib import Path
from typing import Any

from tools.intent_classifier import classify_intent
from tools.report_planner import build_report_spec
from tools.table_profiler import profile_from_rows


REPORTS_DIR = Path(__file__).parent / "reports"
MAX_EMBED_ROWS = 1000


def _serialize(obj: Any) -> Any:
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return str(obj)


def _safe_text(value: Any) -> str:
    return "" if value is None else h(str(value))


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_serialize).replace("</", "<\\/")


def _section_content(sections: list[dict[str, Any]], title: str) -> str:
    for section in sections:
        if section.get("title") == title:
            if section.get("kind") == "paragraph":
                return str(section.get("content", ""))
            if section.get("kind") == "bullets":
                return "\n".join(str(item) for item in section.get("items", []))
    return ""


def _summary_cards_html(cards: list[dict[str, Any]], limit: int = 4) -> str:
    return "".join(
        "<div class=\"card\">"
        f"<div class=\"kpi-value\">{_safe_text(card.get('value'))}</div>"
        f"<div class=\"kpi-label\">{_safe_text(card.get('label'))}</div>"
        f"<div class=\"kpi-note\">{_safe_text(card.get('note'))}</div>"
        "</div>"
        for card in cards[:limit]
    )


def _context_gap_html(gaps: list[dict[str, Any]], required: bool) -> str:
    selected = [gap for gap in gaps if bool(gap.get("required")) is required]
    if not selected:
        label = "No required questions." if required else "No optional questions."
        return f"<div class=\"hint\">{label}</div>"
    return "".join(
        "<div class=\"question-card\">"
        f"<span class=\"context-status\">{'Required' if required else 'Optional'}</span>"
        f"<strong>{_safe_text(gap.get('question'))}</strong>"
        f"<div class=\"hint\">{_safe_text(gap.get('reason'))}</div>"
        f"<div class=\"hint\">Context field: {_safe_text(gap.get('field'))}</div>"
        "</div>"
        for gap in selected
    )


def _mschema_html(mschema: dict[str, Any], limit: int = 18) -> str:
    rows = []
    for col in (mschema.get("columns") or [])[:limit]:
        samples = ", ".join(str(value) for value in (col.get("sample_values") or [])[:4])
        desc = col.get("description") or "No confirmed description yet."
        rows.append(
            "<div class=\"mschema-row\">"
            f"<div class=\"name\">{_safe_text(col.get('name'))}</div>"
            f"<div class=\"meta-line\">Role: {_safe_text(col.get('semantic_role'))} | "
            f"Status: {_safe_text(col.get('status'))} | "
            f"Missing: {_safe_text(col.get('missing_pct'))}%</div>"
            f"<div class=\"meta-line\">{_safe_text(desc)}</div>"
            f"<div class=\"meta-line\">Sample values: {_safe_text(samples or 'not available')}</div>"
            "</div>"
        )
    return "".join(rows) or "<div class=\"hint\">No schema columns were available.</div>"


def _evidence_sql_html(evidence_results: list[dict[str, Any]], preview_sql: str) -> str:
    sql_items = [
        item for item in evidence_results
        if item.get("executed_sql") or item.get("sql")
    ]
    if not sql_items:
        return f"<pre class=\"sql-box\">{_safe_text(preview_sql or 'No SQL evidence was executed.')}</pre>"

    parts = []
    for idx, item in enumerate(sql_items, start=1):
        sql = item.get("executed_sql") or item.get("sql") or ""
        open_attr = " open" if idx == 1 else ""
        parts.append(
            f"<details{open_attr}>"
            f"<summary>{idx}. {_safe_text(item.get('title') or item.get('id') or 'Evidence query')}</summary>"
            f"<pre class=\"sql-box\">{_safe_text(sql)}</pre>"
            "</details>"
        )
    if preview_sql:
        parts.append(
            "<details>"
            "<summary>Preview sample query</summary>"
            f"<pre class=\"sql-box\">{_safe_text(preview_sql)}</pre>"
            "</details>"
        )
    return "<div class=\"sql-stack\">" + "".join(parts) + "</div>"


_CSS = """
*, *::before, *::after { box-sizing: border-box; }
:root {
  --bg:#f3f4f6; --surface:#ffffff; --line:#d8dde6; --text:#242832; --muted:#667085;
  --accent:#00a99d; --accent-2:#2563eb; --warn:#9a6700; --shadow:0 8px 22px rgba(15,23,42,.11);
}
html, body { margin:0; min-height:100%; font-family: Inter, Segoe UI, Arial, sans-serif; color:var(--text); background:var(--bg); }
.topbar { position:sticky; top:0; z-index:20; background:#111827; color:white; padding:16px 28px; box-shadow:var(--shadow); }
.topbar h1 { margin:0; font-size:24px; letter-spacing:0; }
.question { margin-top:6px; color:#d1d5db; font-size:15px; max-width:920px; }
.meta { position:absolute; right:28px; top:20px; color:#aab3c2; font-size:12px; }
.page { padding:20px 28px 34px; display:grid; gap:16px; max-width:1500px; margin:0 auto; }
.answer-card, .card, .slicer-pane, .chart-card, .insight-card, .audit-card { background:var(--surface); border:1px solid var(--line); box-shadow:var(--shadow); border-radius:6px; }
.answer-card { border-left:5px solid var(--accent); padding:18px 20px; }
.answer-title, .block-title { font-size:12px; color:var(--muted); text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
.answer-text { margin-top:8px; font-size:19px; line-height:1.45; max-width:1100px; }
.sample-note { color:var(--warn); font-weight:700; }
.kpi-grid { display:grid; grid-template-columns:repeat(4,minmax(150px,1fr)); gap:12px; }
.card { padding:14px 16px; min-height:94px; border-top:4px solid var(--accent); }
.kpi-value { font-size:28px; color:#1f9d55; font-weight:700; line-height:1.05; }
.kpi-label { margin-top:7px; font-size:12px; color:#111827; text-transform:uppercase; font-weight:800; }
.kpi-note { margin-top:4px; color:var(--muted); font-size:12px; line-height:1.35; }
.executive-grid { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(360px,.9fr); gap:16px; align-items:start; }
.insight-card { padding:16px 18px; }
.point-list { margin:10px 0 0; padding-left:22px; display:grid; gap:10px; }
.point-list li { line-height:1.45; font-size:15px; }
.signals { display:grid; gap:10px; margin-top:12px; }
.signal { border:1px solid #e5e7eb; border-radius:6px; padding:10px 12px; display:grid; gap:5px; }
.signal-head { display:flex; justify-content:space-between; align-items:center; gap:10px; font-weight:800; }
.signal-name { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.signal-score { color:var(--accent-2); font-size:13px; white-space:nowrap; }
.signal-meta { color:var(--muted); font-size:12px; }
.dashboard { display:grid; grid-template-columns:260px minmax(0,1fr); gap:16px; align-items:start; }
.slicer-pane { padding:14px; position:sticky; top:96px; }
.pane-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.pane-title { font-weight:800; color:#374151; text-transform:uppercase; font-size:12px; letter-spacing:.08em; }
.reset-btn { border:1px solid var(--line); background:#f9fafb; border-radius:5px; padding:6px 10px; cursor:pointer; color:#374151; }
.slicer { border-top:1px solid var(--line); padding:12px 0; }
.slicer label { display:block; font-size:12px; font-weight:800; color:#344054; margin-bottom:7px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.slicer select, .slicer input { width:100%; border:1px solid #cfd6df; border-radius:5px; padding:8px 9px; font:inherit; background:white; }
.range-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.hint { color:var(--muted); font-size:12px; margin-top:8px; line-height:1.35; }
.chart-grid { display:grid; grid-template-columns:repeat(2,minmax(310px,1fr)); gap:16px; }
.chart-card { padding:16px; min-height:310px; }
.chart-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; margin-bottom:10px; }
.chart-title { font-size:15px; color:#344054; font-weight:800; }
.chart-body { min-height:245px; display:grid; gap:9px; align-content:center; }
.bar-row { display:grid; grid-template-columns:minmax(130px,210px) 1fr minmax(66px,auto); gap:10px; align-items:center; cursor:pointer; }
.bar-label { font-size:12px; color:#344054; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bar-track { height:20px; background:#edf1f5; border-radius:4px; overflow:hidden; }
.bar-fill { height:100%; background:var(--accent); min-width:2px; }
.bar-value { text-align:right; font-size:12px; color:#344054; white-space:nowrap; }
.bar-row.dimmed { opacity:.32; }
.donut-wrap { display:grid; grid-template-columns:190px minmax(0,1fr); gap:18px; align-items:center; }
.donut-svg { width:190px; height:190px; }
.donut-legend { display:grid; gap:8px; }
.legend-row { display:grid; grid-template-columns:12px minmax(0,1fr) auto; gap:8px; align-items:center; font-size:12px; }
.swatch { width:12px; height:12px; border-radius:2px; }
.audit-grid { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(300px,.8fr); gap:16px; }
.audit-card { padding:16px; min-width:0; }
.sql-box { margin:10px 0 0; background:#111827; color:#e5e7eb; border-radius:6px; padding:14px; overflow:auto; white-space:pre-wrap; line-height:1.45; font-size:12px; max-height:260px; }
.sql-stack { display:grid; gap:10px; margin-top:10px; }
.sql-stack details { border:1px solid #d8dde6; border-radius:6px; background:#f8fafc; overflow:hidden; }
.sql-stack summary { cursor:pointer; padding:9px 12px; font-size:12px; font-weight:800; color:#344054; }
.sql-stack .sql-box { margin:0; border-radius:0; max-height:220px; }
.warning-list { margin:10px 0 0 18px; display:grid; gap:8px; color:#694b11; font-size:13px; }
.context-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(320px,.7fr); gap:16px; align-items:start; }
.question-card { background:white; border:1px solid var(--line); border-left:5px solid var(--warn); border-radius:6px; padding:14px 16px; box-shadow:var(--shadow); display:grid; gap:7px; }
.question-card strong { color:#111827; }
.context-status { display:inline-flex; width:max-content; border-radius:999px; background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; padding:4px 9px; font-size:12px; font-weight:800; text-transform:uppercase; }
.mschema-list { display:grid; gap:8px; max-height:520px; overflow:auto; margin-top:10px; }
.mschema-row { border:1px solid #e5e7eb; border-radius:6px; padding:9px 10px; display:grid; gap:4px; }
.mschema-row .name { font-weight:800; color:#111827; }
.mschema-row .meta-line { color:var(--muted); font-size:12px; }
@media (max-width:1100px) {
  .executive-grid, .dashboard, .audit-grid, .context-grid { grid-template-columns:1fr; }
  .slicer-pane { position:static; }
  .chart-grid { grid-template-columns:1fr; }
  .kpi-grid { grid-template-columns:repeat(2,minmax(150px,1fr)); }
  .meta { position:static; margin-top:6px; }
}
@media (max-width:680px) {
  .page { padding:16px; }
  .topbar { padding:14px 16px; }
  .kpi-grid { grid-template-columns:1fr; }
  .donut-wrap { grid-template-columns:1fr; }
}
"""


_JS = r"""
const RAW_DATA = __DATA_JSON__;
const COLUMNS = __COLUMNS_JSON__;
const SPEC = __SPEC_JSON__;
const ANALYTICS = __ANALYTICS_JSON__;
const state = { filters: {}, cross: {} };
const COLORS = ['#00a99d', '#2563eb', '#f59e0b', '#ef4444', '#7c3aed', '#64748b', '#14b8a6'];

function esc(value) {
  return String(value ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function label(name) { return String(name || '').replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase()); }
function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return esc(value);
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2);
}
function filteredRows() {
  let rows = RAW_DATA.slice();
  for (const slicer of SPEC.slicers || []) {
    const col = slicer.column;
    const f = state.filters[col];
    if (!f) continue;
    if (slicer.type === 'category' && f.value) rows = rows.filter(r => String(r[col] ?? '') === f.value);
    if (slicer.type === 'range') {
      rows = rows.filter(r => {
        const v = num(r[col]);
        if (v === null) return false;
        if (f.min !== '' && f.min !== undefined && v < Number(f.min)) return false;
        if (f.max !== '' && f.max !== undefined && v > Number(f.max)) return false;
        return true;
      });
    }
    if (slicer.type === 'date') {
      rows = rows.filter(r => {
        const v = String(r[col] ?? '').slice(0, 10);
        if (!v) return false;
        if (f.min && v < f.min) return false;
        if (f.max && v > f.max) return false;
        return true;
      });
    }
  }
  for (const [col, value] of Object.entries(state.cross)) {
    rows = rows.filter(r => String(r[col] ?? 'Unknown') === value);
  }
  return rows;
}
function buildSlicers() {
  const root = document.getElementById('slicers');
  if (!root) return;
  const slicers = SPEC.slicers || [];
  if (!slicers.length) {
    root.innerHTML = '<div class="hint">No suitable slicers were detected for this sample.</div>';
    return;
  }
  root.innerHTML = slicers.map(s => {
    if (s.type === 'category') {
      const opts = (s.top_values || []).map(v => `<option value="${esc(v.label)}">${esc(v.label)} (${v.value})</option>`).join('');
      return `<div class="slicer"><label title="${esc(s.column)}">${esc(s.label)}</label><select data-slicer="${esc(s.column)}" data-type="category"><option value="">All</option>${opts}</select></div>`;
    }
    const min = s.min ?? ''; const max = s.max ?? '';
    return `<div class="slicer"><label title="${esc(s.column)}">${esc(s.label)}</label><div class="range-row"><input data-slicer="${esc(s.column)}" data-bound="min" data-type="${s.type}" value="${esc(min)}" placeholder="Min"><input data-slicer="${esc(s.column)}" data-bound="max" data-type="${s.type}" value="${esc(max)}" placeholder="Max"></div></div>`;
  }).join('');
  root.querySelectorAll('[data-slicer]').forEach(el => {
    el.addEventListener('input', onSlicerInput);
    el.addEventListener('change', onSlicerInput);
  });
}
function onSlicerInput(e) {
  const el = e.target;
  const col = el.dataset.slicer;
  const type = el.dataset.type;
  if (!state.filters[col]) state.filters[col] = {};
  if (type === 'category') state.filters[col].value = el.value;
  else state.filters[col][el.dataset.bound] = el.value;
  renderAll();
}
function resetFilters() {
  state.filters = {}; state.cross = {};
  document.querySelectorAll('[data-slicer]').forEach(el => {
    if (el.tagName === 'SELECT') el.value = '';
    else el.value = '';
  });
  renderAll();
}
function updateFilteredSummary(rows) {
  const el = document.getElementById('filtered-summary');
  if (!el) return;
  const total = RAW_DATA.length;
  if (!(SPEC.slicers || []).length) {
    el.textContent = `Embedded audit sample: ${total.toLocaleString()} rows. Main evidence comes from aggregate/profile results.`;
    return;
  }
  const active = Object.keys(state.filters).filter(k => {
    const f = state.filters[k];
    return f && Object.values(f).some(v => v !== '' && v !== undefined);
  }).length + Object.keys(state.cross).length;
  el.textContent = `Filtered sample: ${rows.length.toLocaleString()} of ${total.toLocaleString()} embedded rows. Active filters: ${active}.`;
}
function updateKpis(rows) {
  const root = document.getElementById('kpi-grid');
  if (!root) return;
  const cards = SPEC.summary_cards || [];
  
  // Find top measure columns from candidate signals
  const measures = (SPEC.candidate_signals || [])
    .filter(sig => String(sig.role || '').toLowerCase() === 'measure')
    .map(sig => sig.column);
  
  const dynamic = [
    { label: 'Filtered Sample', value: rows.length.toLocaleString(), note: 'Rows after interactive filters' },
  ];
  
  // Compute dynamic average for the top 2 candidate measures
  let measureCount = 0;
  for (const col of measures) {
    if (measureCount >= 2) break;
    // Check if col is in the rows
    if (rows.length && col in rows[0]) {
      let sum = 0;
      let cnt = 0;
      for (const r of rows) {
        const v = num(r[col]);
        if (v !== null) {
          sum += v;
          cnt++;
        }
      }
      if (cnt > 0) {
        const avg = sum / cnt;
        dynamic.push({
          label: 'Avg ' + label(col),
          value: formatNumber(avg),
          note: 'Average calculated from filtered sample (' + cnt.toLocaleString() + ' rows)'
        });
        measureCount++;
      }
    }
  }
  
  // Fallback to static cards if we don't have enough dynamic indicators
  const merged = [...dynamic];
  for (const card of cards) {
    if (merged.length >= 4) break;
    // Don't duplicate 'Rows' or similar labels if they are already represented dynamically
    if (card.label === 'Rows') continue; 
    merged.push(card);
  }
  
  root.innerHTML = merged.slice(0, 4).map(c => `<div class="card"><div class="kpi-value">${esc(c.value)}</div><div class="kpi-label">${esc(c.label)}</div><div class="kpi-note">${esc(c.note || '')}</div></div>`).join('');
}
function aggregate(rows, chart) {
  if (chart.id === 'candidate_missingness') {
    const cols = chart.columns || [];
    return cols.map(col => {
      const missingCount = rows.filter(r => r[col] === null || r[col] === undefined || r[col] === '').length;
      const pct = rows.length ? (missingCount / rows.length * 100) : 0;
      return { label: col, value: Math.round(pct * 10) / 10 };
    });
  }
  if (chart.data) return chart.data.map(d => ({ label: d.label, value: Number(d.value) || 0 }));
  const dim = chart.dimension;
  const metric = chart.metric;
  const groups = new Map();
  for (const r of rows) {
    const key = String(r[dim] ?? 'Unknown');
    if (!groups.has(key)) groups.set(key, { count: 0, sum: 0 });
    const g = groups.get(key);
    g.count++;
    if (metric && metric !== '__count__') {
      const n = num(r[metric]);
      if (n !== null) g.sum += n;
    }
  }
  return [...groups.entries()].map(([key, g]) => ({
    label: key,
    value: chart.aggregation === 'avg' && g.count ? g.sum / g.count : g.count,
  })).sort((a,b) => b.value - a.value).slice(0, 12);
}
function renderBarChart(root, chart, rows) {
  const data = aggregate(rows, chart);
  const max = Math.max(...data.map(d => d.value), 1);
  root.innerHTML = data.map(d => {
    const width = Math.max(2, d.value / max * 100);
    const dimmed = state.cross[chart.dimension] && state.cross[chart.dimension] !== d.label;
    return `<div class="bar-row ${dimmed ? 'dimmed' : ''}" data-chart-dim="${esc(chart.dimension || '')}" data-value="${esc(d.label)}"><div class="bar-label" title="${esc(d.label)}">${esc(d.label)}</div><div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div><div class="bar-value">${formatNumber(d.value)}</div></div>`;
  }).join('') || '<div class="hint">No data after filters.</div>';
  root.querySelectorAll('[data-chart-dim]').forEach(row => row.addEventListener('click', () => {
    const dim = row.dataset.chartDim;
    if (!dim) return;
    const value = row.dataset.value;
    if (state.cross[dim] === value) delete state.cross[dim];
    else state.cross[dim] = value;
    renderAll();
  }));
}
function donutPath(cx, cy, r, start, end) {
  const large = end - start > Math.PI ? 1 : 0;
  const sx = cx + r * Math.cos(start);
  const sy = cy + r * Math.sin(start);
  const ex = cx + r * Math.cos(end);
  const ey = cy + r * Math.sin(end);
  return `M ${cx} ${cy} L ${sx} ${sy} A ${r} ${r} 0 ${large} 1 ${ex} ${ey} Z`;
}
function renderDonutChart(root, chart, rows) {
  const data = aggregate(rows, chart).filter(d => d.value > 0).slice(0, 7);
  const total = data.reduce((sum, item) => sum + item.value, 0);
  if (!total) { root.innerHTML = '<div class="hint">No data after filters.</div>'; return; }
  let angle = -Math.PI / 2;
  const slices = data.map((d, i) => {
    const next = angle + (d.value / total) * Math.PI * 2;
    const path = donutPath(100, 100, 90, angle, next);
    angle = next;
    return `<path d="${path}" fill="${COLORS[i % COLORS.length]}"><title>${esc(d.label)}: ${formatNumber(d.value)}</title></path>`;
  }).join('');
  const legend = data.map((d, i) => `<div class="legend-row"><span class="swatch" style="background:${COLORS[i % COLORS.length]}"></span><span title="${esc(d.label)}">${esc(d.label)}</span><span>${formatNumber(d.value)}</span></div>`).join('');
  root.innerHTML = `<div class="donut-wrap"><svg class="donut-svg" viewBox="0 0 200 200">${slices}<circle cx="100" cy="100" r="48" fill="white"></circle></svg><div class="donut-legend">${legend}</div></div>`;
}
function renderCharts(rows) {
  const root = document.getElementById('chart-grid');
  if (!root) return;
  const charts = SPEC.charts || [];
  if (!charts.length) {
    root.innerHTML = '<div class="chart-card"><div class="hint">No chart was rendered because no chart would improve this answer.</div></div>';
    return;
  }
  root.innerHTML = charts.map((c, i) => `<div class="chart-card"><div class="chart-head"><div class="chart-title">${esc(c.title)}</div><div class="hint">${esc(c.aggregation || '')}</div></div><div class="chart-body" id="chart-${i}"></div></div>`).join('');
  charts.forEach((chart, i) => {
    const body = document.getElementById(`chart-${i}`);
    if (chart.type === 'donut') renderDonutChart(body, chart, rows);
    else renderBarChart(body, chart, rows);
  });
}
function renderAll() {
  const rows = filteredRows();
  updateFilteredSummary(rows);
  updateKpis(rows);
  renderCharts(rows);
}
document.addEventListener('DOMContentLoaded', () => {
  buildSlicers();
  const reset = document.getElementById('reset-filters');
  if (reset) reset.addEventListener('click', resetFilters);
  renderAll();
});
"""


def generate_report(
    question: str,
    sql_query: str,
    raw_data: list,
    columns: list,
    analytics: dict,
    report_spec: dict | None = None,
    table_profile: dict | None = None,
) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"report_{timestamp}_{uuid.uuid4().hex[:8]}.html"
    filepath = REPORTS_DIR / filename
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if report_spec is None:
        table_profile = table_profile or profile_from_rows(raw_data, columns)
        intent = classify_intent(question)
        report_spec = build_report_spec(question, intent, table_profile, len(raw_data))

    if report_spec.get("mode") == "context_required":
        sections = report_spec.get("sections", [])
        direct_answer = _section_content(sections, "Direct Answer") or (
            "Semantic context is required before this question can be answered reliably."
        )
        gaps = report_spec.get("semantic_gaps", [])
        mschema = report_spec.get("mschema", {})
        context = report_spec.get("semantic_context", {})
        scope = report_spec.get("data_scope", {})
        cards_html = _summary_cards_html(report_spec.get("summary_cards", []))
        warnings_html = "".join(f"<li>{_safe_text(w)}</li>" for w in report_spec.get("warnings", [])[:5])
        context_rows = [
            ("Table purpose", context.get("table_purpose") or "Not confirmed"),
            ("Row grain", context.get("row_grain") or "Not confirmed"),
            ("Primary metric", context.get("primary_metric") or "Not confirmed"),
            ("Outcome column", context.get("outcome_column") or "Not confirmed"),
            ("Positive outcome value", context.get("positive_outcome_value") or "Not confirmed"),
        ]
        context_html = "".join(
            "<div class=\"mschema-row\">"
            f"<div class=\"name\">{_safe_text(label)}</div>"
            f"<div class=\"meta-line\">{_safe_text(value)}</div>"
            "</div>"
            for label, value in context_rows
        )
        spec_json = _json_payload(report_spec)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Context Required - {h(generated_at)}</title>
<style>{_CSS}</style>
</head>
<body>
  <header class="topbar">
    <h1>Context Required</h1>
    <div class="question">{_safe_text(question)}</div>
    <div class="meta">Generated {h(generated_at)}</div>
  </header>

  <main class="page">
    <section class="answer-card">
      <div class="answer-title">AI Answer</div>
      <div class="answer-text">{_safe_text(direct_answer)}</div>
      <div class="hint sample-note">The system stopped on purpose because business meaning is not confirmed. This avoids a long but unreliable report.</div>
    </section>

    <section class="kpi-grid">{cards_html}</section>

    <section class="context-grid">
      <div class="insight-card">
        <div class="block-title">Required Questions</div>
        <div class="signals">{_context_gap_html(gaps, True)}</div>
      </div>
      <div class="insight-card">
        <div class="block-title">Current Semantic Context</div>
        <div class="mschema-list">{context_html}</div>
      </div>
    </section>

    <section class="context-grid">
      <div class="insight-card">
        <div class="block-title">Optional Questions</div>
        <div class="signals">{_context_gap_html(gaps, False)}</div>
      </div>
      <div class="insight-card">
        <div class="block-title">M-Schema Draft</div>
        <div class="hint">Auto-detected roles are only hints. Confirmed semantic context will override these drafts in future reports.</div>
        <div class="mschema-list">{_mschema_html(mschema)}</div>
      </div>
    </section>

    <section class="audit-grid">
      <div class="audit-card">
        <div class="block-title">SQL Query Used For Preview</div>
        <pre class="sql-box">{_safe_text(sql_query or 'No preview query was executed because semantic context is required first.')}</pre>
      </div>
      <div class="audit-card">
        <div class="block-title">Scope & Limitations</div>
        <div class="hint">Full scope rows: {_safe_text(scope.get("total_rows"))}; profiled columns: {_safe_text(scope.get("profiled_columns"))}; total columns: {_safe_text(scope.get("total_columns"))}.</div>
        <ul class="warning-list">{warnings_html or '<li>No additional warnings.</li>'}</ul>
      </div>
    </section>

    <script type="application/json" id="report-spec-json">{spec_json}</script>
  </main>
</body>
</html>
"""
        filepath.write_text(html, encoding="utf-8")
        return str(filepath)

    sections = report_spec.get("sections", [])
    direct_answer = _section_content(sections, "Direct Answer") or analytics.get("summary", "")
    warnings = report_spec.get("warnings", [])
    warning_html = "".join(f"<li>{_safe_text(w)}</li>" for w in warnings[:5])
    executive_points = report_spec.get("executive_points") or analytics.get("insights", [])
    points_html = "".join(f"<li>{_safe_text(point)}</li>" for point in executive_points[:5])
    candidate_signals = report_spec.get("candidate_signals", [])
    signals_html = "".join(
        "<div class=\"signal\">"
        f"<div class=\"signal-head\"><span class=\"signal-name\" title=\"{_safe_text(signal.get('column'))}\">{_safe_text(signal.get('rank'))}. {_safe_text(signal.get('column'))}</span>"
        f"<span class=\"signal-score\">Score {_safe_text(signal.get('score'))}</span></div>"
        f"<div class=\"signal-meta\">{_safe_text(signal.get('role'))} - Missing {_safe_text(signal.get('missing'))}</div>"
        f"<div class=\"hint\">{_safe_text(signal.get('reason'))}</div>"
        "</div>"
        for signal in candidate_signals[:5]
    )
    scope = report_spec.get("data_scope", {})
    slicers = report_spec.get("slicers", [])
    charts = report_spec.get("charts", [])
    sample_rows = raw_data[:MAX_EMBED_ROWS] if slicers else raw_data[:5]
    evidence_sql_html = _evidence_sql_html(report_spec.get("evidence_results", []), sql_query)
    if slicers:
        dashboard_html = f"""
    <div class="dashboard">
      <aside class="slicer-pane">
        <div class="pane-head">
          <div class="pane-title">Slicers</div>
          <button class="reset-btn" id="reset-filters">Reset</button>
        </div>
        <div class="hint">Only the most relevant 1-2 slicers are shown. Filters affect the embedded sample and supporting charts.</div>
        <div id="slicers"></div>
      </aside>

      <section>
        <div class="chart-grid" id="chart-grid"></div>
      </section>
    </div>
"""
    else:
        dashboard_html = """
    <section>
      <div class="chart-grid" id="chart-grid"></div>
    </section>
"""
    spec_json = _json_payload(report_spec)
    data_json = _json_payload(sample_rows)
    cols_json = _json_payload(columns)
    analytics_json = _json_payload(analytics or {})
    js = (_JS
          .replace("__DATA_JSON__", data_json)
          .replace("__COLUMNS_JSON__", cols_json)
          .replace("__SPEC_JSON__", spec_json)
          .replace("__ANALYTICS_JSON__", analytics_json))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Maxxem Data Analysis Report - {h(generated_at)}</title>
<style>{_CSS}</style>
</head>
<body>
  <header class="topbar">
    <h1>Maxxem Data Analysis Report</h1>
    <div class="question">{_safe_text(question)}</div>
    <div class="meta">Generated {h(generated_at)}</div>
  </header>

  <main class="page">
    <section class="answer-card">
      <div class="answer-title">AI Answer</div>
      <div class="answer-text">{_safe_text(direct_answer)}</div>
      <div class="hint sample-note" id="filtered-summary">Interactive filters apply to embedded sample rows only.</div>
    </section>

    <section class="kpi-grid" id="kpi-grid"></section>

    <section class="executive-grid">
      <div class="insight-card">
        <div class="block-title">5 Executive Points</div>
        <ol class="point-list">{points_html}</ol>
      </div>
      <div class="insight-card">
        <div class="block-title">Candidate Signals</div>
        <div class="signals">{signals_html or '<div class="hint">No strong candidate signals were detected.</div>'}</div>
      </div>
    </section>

    {dashboard_html}

    <section class="audit-grid">
      <div class="audit-card">
        <div class="block-title">SQL Evidence</div>
        {evidence_sql_html}
      </div>
      <div class="audit-card">
        <div class="block-title">Scope & Limitations</div>
        <div class="hint">Full scope rows: {_safe_text(scope.get("total_rows"))}; embedded sample rows: {len(sample_rows):,}; profiled columns: {_safe_text(scope.get("profiled_columns"))}.</div>
        <ul class="warning-list">{warning_html or '<li>No major profiling limitations were detected.</li>'}</ul>
      </div>
    </section>

    <script type="application/json" id="analytics-json">{analytics_json}</script>
    <script type="application/json" id="report-spec-json">{spec_json}</script>
    <script>{js}</script>
  </main>
</body>
</html>
"""
    filepath.write_text(html, encoding="utf-8")
    return str(filepath)
