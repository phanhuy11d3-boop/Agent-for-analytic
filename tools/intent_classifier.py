import re
import unicodedata
from typing import Any


INTENTS = {
    "definition": [
        "what is", "what are", "define", "criteria", "meaning", "quality",
        "the nao", "la gi", "dinh nghia", "tieu chi", "uy tin",
    ],
    "ranking": ["top", "bottom", "rank", "highest", "lowest", "most", "least", "best", "worst", "xep hang", "cao nhat", "thap nhat"],
    "segmentation": [
        "segment", "group", "breakdown", "frequency distribution", "distribution",
        "frequency", "frequencies", "histogram", "phan khuc", "nhom", "phan bo",
    ],
    "trend": ["trend", "over time", "time series", "monthly", "daily", "weekly", "xu huong", "theo thoi gian"],
    "comparison": ["compare", "versus", "vs", "difference", "between", "so sanh", "khac nhau"],
    "driver_analysis": ["why", "driver", "factor", "impact", "affect", "cause", "nguyen nhan", "yeu to", "anh huong"],
    "feature_selection": [
        "which column", "which columns", "which feature", "which features",
        "important column", "important feature", "predict", "explain", "determine",
        "cot nao", "column nao", "nhung column", "nhung cot", "feature nao",
        "quyet dinh", "du doan", "giai thich", "anh huong",
    ],
    "anomaly": ["anomaly", "outlier", "unusual", "abnormal", "bat thuong", "ngoai le"],
    "data_quality": ["missing", "null", "quality", "duplicate", "clean", "chat luong", "thieu", "trung lap"],
    "decision_readiness": ["should", "decision", "recommend", "approve", "ready", "nen", "khuyen nghi", "ra quyet dinh"],
}


def _normalise(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def classify_intent(question: str) -> dict[str, Any]:
    normalized = _normalise(question)
    scores: dict[str, int] = {}

    for intent, phrases in INTENTS.items():
        score = 0
        for phrase in phrases:
            norm_phrase = _normalise(phrase)
            if norm_phrase and norm_phrase in normalized:
                score += 2 if " " in norm_phrase else 1
        if score:
            scores[intent] = score

    if not scores:
        intent = "segmentation"
        confidence = 0.35
    else:
        intent = max(scores.items(), key=lambda item: item[1])[0]
        confidence = min(0.95, 0.45 + scores[intent] * 0.1)

    return {
        "intent": intent,
        "confidence": round(confidence, 2),
        "normalized_question": normalized,
        "matched_intents": scores,
        "requires_validation": intent in {"definition", "driver_analysis", "feature_selection", "decision_readiness"},
    }
