"""Deterministic news-event classifier — tags a headline to a catalyst category by keyword (FREE, no
LLM, zero tokens). Same philosophy as the sentiment lexicon: transparent, auditable, pure. Feeds the
analyst's catalyst log and the (tiny) verdict digest, so "what actually happened" is structured without
spending a single token. Crude by design — the headline text travels with the tag, so the read is checkable.
"""

import re

# Order = priority (first match wins). Long keywords (>=5 chars) match as substrings; short ones must
# be whole words (so "fine" doesn't fire on "define", "q4" only on the token).
_EVENT_RULES: list[tuple[str, set[str]]] = [
    ("legal/probe",     {"fraud", "probe", "lawsuit", "bail", "court", "penalty", "raid", "sebi",
                         "investigat", "arrest", "detention", "bribery", "scam", "tribunal", "verdict",
                         "appeal", "fine"}),
    ("results",         {"results", "earnings", "profit", "quarterly", "q1", "q2", "q3", "q4",
                         "revenue", "margin", "ebitda", "topline", "net income", "miss", "beat"}),
    ("guidance/outlook",{"guidance", "outlook", "forecast", "expects", "projects", "sees"}),
    ("deal/order",      {"order", "contract", "wins", "secures", "bags", "acquisition", "acquire",
                         "stake", "merger", "joint venture", "partnership", "tie-up"}),
    ("rating",          {"upgrade", "downgrade", "rating", "overweight", "underweight", "price target",
                         "initiate"}),
    ("management",      {"resign", "appoint", "ceo", "cfo", "chairman", "steps down", "quits", "board"}),
    ("capex/expansion", {"capex", "expansion", "capacity", "plant", "factory", "commission", "greenfield"}),
    ("capital",         {"dividend", "buyback", "bonus", "rights issue", "qip", "fundrais", "stake sale"}),
    ("regulatory",      {"approval", "approved", "regulat", "clearance", "license", "tariff"}),
]
_WORD = re.compile(r"[a-z&/'-]+")


def classify_event(title: str | None) -> str | None:
    """The single best-matching catalyst category for a headline, or None if nothing matches."""
    t = (title or "").lower()
    toks = set(_WORD.findall(t))
    for label, kws in _EVENT_RULES:
        for k in kws:
            if (k in t) if len(k) >= 5 else (k in toks):
                return label
    return None


def catalysts(headlines: list[dict]) -> list[dict]:
    """Headlines [{title,polarity,published}] → tagged catalyst log (only those that match an event)."""
    out = []
    for h in headlines:
        ev = classify_event(h.get("title"))
        if ev:
            out.append({"date": h.get("published"), "event": ev,
                        "title": h.get("title"), "polarity": h.get("polarity")})
    return out
