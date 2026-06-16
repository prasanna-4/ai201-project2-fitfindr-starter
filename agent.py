"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

_SIZE_WORDS = ["XXS", "XS", "XXL", "XL", "S", "M", "L"]


def _parse_query(query: str) -> dict:
    """
    Extract a description, size, and max_price from a free-text query using regex.

    Regex (not the LLM) is used because the targets are simple and deterministic,
    and it keeps the loop fast and offline. Returns a dict with keys
    description / size / max_price.
    """
    text = query or ""
    leftover = text

    # max_price: "under $30", "below 40", "less than $25", "max 20", then bare "$30".
    max_price = None
    m = re.search(r"(?:under|below|less than|max|cheaper than|<)\s*\$?\s*(\d+(?:\.\d+)?)",
                  text, re.IGNORECASE)
    if not m:
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", text)
    if m:
        max_price = float(m.group(1))
        leftover = leftover.replace(m.group(0), " ")

    # size: explicit "size M" / "size 8" first, then a standalone size word.
    size = None
    sm = re.search(r"\bsize\s+([A-Za-z0-9/.]+)", text, re.IGNORECASE)
    if sm:
        size = sm.group(1).upper()
        leftover = leftover.replace(sm.group(0), " ")
    else:
        for word in _SIZE_WORDS:
            if re.search(rf"\b{word}\b", leftover):
                size = word
                leftover = re.sub(rf"\b{word}\b", " ", leftover, count=1)
                break

    # description: what's left, with filler trimmed.
    description = re.sub(r"\s+", " ", leftover).strip(" ,.")

    return {"description": description, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """Initialize and return a fresh session dict for one user interaction."""
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "adjustments": [],           # notes about filters the loop loosened
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request.
        wardrobe: User's wardrobe dict (get_example_wardrobe / get_empty_wardrobe).

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.
    """
    session = _new_session(query, wardrobe)

    # Step 0: guard against an empty query.
    if not query or not query.strip():
        session["error"] = "Tell me what you're looking for — e.g. 'vintage tee under $30, size M'."
        return session

    # Step 1: parse the query into search parameters.
    parsed = _parse_query(query)
    session["parsed"] = parsed
    desc, size, max_price = parsed["description"], parsed["size"], parsed["max_price"]

    # Step 2: search.
    results = search_listings(desc, size=size, max_price=max_price)

    # Step 3: branch on the result — retry with the size filter loosened before giving up.
    if not results and size is not None:
        results = search_listings(desc, size=None, max_price=max_price)
        if results:
            session["adjustments"].append(
                f"No '{size}' matches, so I searched all sizes — double-check the fit."
            )

    session["search_results"] = results

    if not results:
        price_note = f" under ${max_price:g}" if max_price is not None else ""
        session["error"] = (
            f"No listings matched \"{desc}\"{price_note}. "
            "Try broader keywords (e.g. 'tee' instead of 'vintage graphic tee'), "
            "removing the price cap, or a different style."
        )
        return session  # <-- early exit: suggest_outfit / create_fit_card never run.

    # Step 4: select the top-ranked item.
    session["selected_item"] = results[0]

    # Step 5: suggest an outfit (handles empty wardrobe internally).
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)

    # Step 6: write the fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        if session["adjustments"]:
            print(f"Note: {' '.join(session['adjustments'])}\n")
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    print(f"fit_card is None: {session2['fit_card'] is None}")

    print("\n\n=== Retry-fallback path (impossible size, possible item) ===\n")
    session3 = run_agent(
        query="leather bomber jacket size XXL",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Adjustments: {session3['adjustments']}")
    print(f"Selected: {session3['selected_item']['title'] if session3['selected_item'] else None}")
