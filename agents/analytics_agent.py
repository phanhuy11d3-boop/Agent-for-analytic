import json
import re
import pandas as pd
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config import GROQ_API_KEY, GROQ_MODEL

SYSTEM_PROMPT = """You are a Senior Data Analyst specializing in fraud detection and retail analytics.
You receive pre-computed statistics (NOT raw rows) from the full dataset. Interpret the numbers accurately.

Return ONLY a valid JSON object (no markdown, no extra text) with this exact structure:
{
  "summary": "2-3 sentences summarizing the key finding",
  "kpis": [
    {"name": "KPI name", "value": "formatted value", "interpretation": "what this means"}
  ],
  "insights": ["insight 1", "insight 2", "insight 3"],
  "anomalies": ["anomaly if any — empty list if none"],
  "recommendation": "One actionable recommendation based on findings — format: Finding → So What → Now What",
  "hypotheses": [
    {
      "factor": "variable name or pattern observed in data",
      "signal": "strong",
      "effect": "observed association (e.g. 'fraud rate 12% higher in Electronics')",
      "note": "hypothesis to investigate further with controlled experiment"
    }
  ]
}

Guidelines:
- kpis: derive from the STATISTICS PROVIDED — cite actual numbers (mean, std, percentiles)
- insights: reference p-values and chi-square results when provided — e.g. "significant difference (p=0.003)" or "no significant difference (p=0.42)"
- recommendation: follow the structure "Finding → So What → Now What"
- hypotheses: list 2-4 signals observed in the data; signal = "strong" (p<0.05) or "weak" (p>=0.05 or not tested)
  * These are HYPOTHESES, not proven causes — always phrase as "data suggests", "associated with", "worth investigating"
  * NEVER claim causation — only correlation/association from observational data
  * Example: "Electronics category associated with 12% higher fraud rate (p=0.001) — worth investigating if product type drives risk or if it correlates with high-value transactions"
"""


def _compute_stats(raw_data: list, columns: list) -> dict:
    """Compute aggregated statistics from the full dataset to send to LLM."""
    if not raw_data:
        return {}
    df = pd.DataFrame(raw_data)

    stats: dict = {"row_count": len(df), "columns": {}}

    # Detect fraud/flag column
    FLAG_KW = {"flag", "fraud", "churn", "default", "label", "target"}
    flag_col = None
    for c in columns:
        if c not in df.columns:
            continue
        lower = c.lower()
        if any(kw in lower for kw in FLAG_KW) or lower.startswith(("is_", "has_")):
            vals = df[c].dropna().unique()
            if set(str(v) for v in vals) <= {"0", "1", "True", "False", "true", "false"}:
                flag_col = c
                break

    if flag_col and flag_col in df.columns:
        df[flag_col] = pd.to_numeric(df[flag_col], errors="coerce")
        stats["fraud_rate_overall"] = round(df[flag_col].mean() * 100, 3)

    for col in columns:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if len(series) == 0:
            continue

        if pd.api.types.is_numeric_dtype(series) and col != flag_col:
            stats["columns"][col] = {
                "type": "numeric",
                "count": int(len(series)),
                "mean": round(float(series.mean()), 4),
                "median": round(float(series.median()), 4),
                "std": round(float(series.std()), 4),
                "min": round(float(series.min()), 4),
                "max": round(float(series.max()), 4),
                "p25": round(float(series.quantile(0.25)), 4),
                "p75": round(float(series.quantile(0.75)), 4),
            }
        elif col != flag_col:
            nunique = int(series.nunique())
            if 1 < nunique <= 30:
                vc = series.value_counts().head(10).to_dict()
                col_stat: dict = {
                    "type": "categorical",
                    "nunique": nunique,
                    "top_values": {str(k): int(v) for k, v in vc.items()},
                }
                # Fraud rate breakdown by category (if flag col exists)
                if flag_col and flag_col in df.columns:
                    fraud_by_cat = (
                        df.groupby(col)[flag_col]
                        .mean()
                        .round(4)
                        .sort_values(ascending=False)
                        .head(10)
                        .to_dict()
                    )
                    col_stat["fraud_rate_by_value"] = {str(k): round(v * 100, 2) for k, v in fraud_by_cat.items()}
                stats["columns"][col] = col_stat

    return stats


def _chi_square_tests(raw_data: list, columns: list) -> list:
    """Run chi-square tests between categorical columns and the flag/target column."""
    try:
        from scipy.stats import chi2_contingency
    except ImportError:
        return []

    if not raw_data:
        return []

    df = pd.DataFrame(raw_data)

    FLAG_KW = {"flag", "fraud", "churn", "default", "label", "target"}
    flag_col = None
    for c in columns:
        if c not in df.columns:
            continue
        lower = c.lower()
        if any(kw in lower for kw in FLAG_KW) or lower.startswith(("is_", "has_")):
            vals = df[c].dropna().unique()
            if set(str(v) for v in vals) <= {"0", "1", "True", "False", "true", "false"}:
                flag_col = c
                break

    if not flag_col or flag_col not in df.columns:
        return []

    df[flag_col] = pd.to_numeric(df[flag_col], errors="coerce")
    results = []

    for col in columns:
        if col not in df.columns or col == flag_col:
            continue
        series = df[col].dropna()
        if not pd.api.types.is_object_dtype(series) and not pd.api.types.is_categorical_dtype(series):
            continue
        nunique = series.nunique()
        if not (2 <= nunique <= 20):
            continue

        try:
            ct = pd.crosstab(df[col], df[flag_col])
            if ct.shape[1] < 2:
                continue
            chi2, p, dof, _ = chi2_contingency(ct)
            results.append({
                "comparison": f"{col} vs {flag_col}",
                "chi2": round(float(chi2), 3),
                "p_value": round(float(p), 4),
                "dof": int(dof),
                "significant": p < 0.05,
                "interpretation": (
                    f"Significant association between {col} and {flag_col} (p={p:.4f})"
                    if p < 0.05
                    else f"No significant association between {col} and {flag_col} (p={p:.4f})"
                ),
            })
        except Exception:
            continue

    return results


def _format_stats_for_llm(stats: dict, chi_sq: list) -> str:
    if not stats:
        return "No data."

    lines = [f"Dataset: {stats.get('row_count', 0):,} rows total"]

    overall_fraud = stats.get("fraud_rate_overall")
    if overall_fraud is not None:
        lines.append(f"Overall fraud rate: {overall_fraud}%")

    lines.append("\n--- Column Statistics ---")
    for col, col_stats in stats.get("columns", {}).items():
        if col_stats["type"] == "numeric":
            lines.append(
                f"{col}: mean={col_stats['mean']}, median={col_stats['median']}, "
                f"std={col_stats['std']}, range=[{col_stats['min']}, {col_stats['max']}], "
                f"IQR=[{col_stats['p25']}, {col_stats['p75']}]"
            )
        else:
            top = ", ".join(f"{k}({v})" for k, v in list(col_stats["top_values"].items())[:5])
            lines.append(f"{col}: {col_stats['nunique']} unique values — top: {top}")
            if "fraud_rate_by_value" in col_stats:
                fraud_breakdown = ", ".join(
                    f"{k}={v}%" for k, v in list(col_stats["fraud_rate_by_value"].items())[:6]
                )
                lines.append(f"  → fraud rate by {col}: {fraud_breakdown}")

    if chi_sq:
        lines.append("\n--- Statistical Tests (Chi-Square) ---")
        for r in chi_sq:
            sig = "✓ SIGNIFICANT" if r["significant"] else "✗ not significant"
            lines.append(f"{r['comparison']}: chi2={r['chi2']}, p={r['p_value']} — {sig}")

    return "\n".join(lines)


def run_analytics(raw_data: list, columns: list, question: str, sql_query: str = "") -> dict:
    if not raw_data:
        return {"summary": "No data.", "kpis": [], "insights": [], "anomalies": [], "recommendation": "", "hypotheses": []}

    stats = _compute_stats(raw_data, columns)
    chi_sq = _chi_square_tests(raw_data, columns)
    data_str = _format_stats_for_llm(stats, chi_sq)

    llm = ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY, temperature=0.2)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Original question: {question}\n\n"
            f"SQL used:\n{sql_query}\n\n"
            f"Pre-computed statistics from {len(raw_data):,} rows:\n{data_str}"
        )),
    ]

    response = llm.invoke(messages).content
    result = _parse_json_response(response)

    # Normalise key: accept both old 'causal_attribution' and new 'hypotheses'
    if "causal_attribution" in result and "hypotheses" not in result:
        result["hypotheses"] = result.pop("causal_attribution")

    return result


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {
            "summary": text[:500],
            "kpis": [],
            "insights": [text],
            "anomalies": [],
            "recommendation": "",
            "hypotheses": [],
        }


def analytics_agent_node(state: dict) -> dict:
    question  = state["question"]
    raw_data  = state.get("raw_data", [])
    columns   = state.get("columns", [])
    sql_query = state.get("sql_query", "")

    if not raw_data:
        step = {
            "agent": "Analytics Agent",
            "icon": "📊",
            "status": "error",
            "message": "No data to analyze — skipping analytics",
        }
        return {
            **state,
            "analytics": None,
            "steps": state.get("steps", []) + [step],
        }

    analytics = run_analytics(raw_data, columns, question, sql_query)

    step = {
        "agent": "Analytics Agent",
        "icon": "📊",
        "status": "success",
        "kpi_count": len(analytics.get("kpis", [])),
        "insight_count": len(analytics.get("insights", [])),
        "message": f"Generated {len(analytics.get('kpis', []))} KPIs and {len(analytics.get('insights', []))} insights",
    }

    return {
        **state,
        "analytics": analytics,
        "steps": state.get("steps", []) + [step],
    }
