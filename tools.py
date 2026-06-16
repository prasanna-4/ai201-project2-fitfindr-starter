"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# Words that carry no search signal — stripped before keyword scoring.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "in", "on", "of", "to", "and", "or",
    "my", "me", "i", "im", "i'm", "looking", "want", "need", "find", "some",
    "something", "under", "below", "less", "than", "max", "over", "size",
    "sized", "thats", "that", "is", "are", "be", "out", "there", "thrifted",
    "secondhand", "good", "nice", "cute", "really", "kind", "sort",
}


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(messages: list[dict], temperature: float, max_tokens: int = 400) -> str:
    """Single Groq chat completion. Raises on API error (callers catch)."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _size_matches(requested: str | None, listing_size: str) -> bool:
    """
    Case-insensitive token match. Splits the listing size into alphanumeric
    tokens so "M" matches "S/M", and "8" matches "US 8".
    """
    if not requested:
        return True
    req_tokens = [t for t in re.split(r"[^A-Za-z0-9]+", requested.upper()) if t]
    listing_tokens = [t for t in re.split(r"[^A-Za-z0-9]+", listing_size.upper()) if t]
    return any(t in listing_tokens for t in req_tokens)


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform
    """
    try:
        listings = load_listings()
    except Exception:
        # Data file missing/corrupt — fail to an empty result, never crash.
        return []

    keywords = _tokenize(description or "")
    scored = []

    for item in listings:
        # 1. Hard filters.
        if max_price is not None and item.get("price", 0) > max_price:
            continue
        if not _size_matches(size, item.get("size", "")):
            continue

        # 2. Build the searchable text for this listing.
        haystack_parts = [
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            item.get("brand") or "",
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
        ]
        haystack = " ".join(haystack_parts).lower()
        tags = {t.lower() for t in item.get("style_tags", [])}

        # 3. Score by keyword overlap. Exact style-tag hits weigh extra.
        score = 0
        for kw in keywords:
            if kw in tags:
                score += 3
            elif kw in haystack:
                score += 1

        # No keywords at all → treat every (filtered) listing as a weak match
        # so a bare "size M under $20" query still returns something.
        if not keywords:
            score = 1

        if score > 0:
            scored.append((score, item))

    # 4. Sort by score (desc), tie-break on price (cheaper first).
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("price", 0)))
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def _format_item(item: dict) -> str:
    """One-line human summary of a listing for prompting."""
    tags = ", ".join(item.get("style_tags", []))
    colors = ", ".join(item.get("colors", []))
    return (
        f"{item.get('title', 'item')} "
        f"(category: {item.get('category', '?')}; colors: {colors}; "
        f"style: {tags}; ${item.get('price', '?')} on {item.get('platform', '?')})"
    )


def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.
    """
    if not isinstance(new_item, dict) or not new_item:
        return "I can't suggest an outfit without an item to style. Try searching first."

    items = (wardrobe or {}).get("items", [])
    item_summary = _format_item(new_item)

    if not items:
        # Empty-wardrobe branch: general advice, not a crash.
        prompt = (
            f"A shopper is considering this secondhand piece:\n  {item_summary}\n\n"
            "They have NOT saved a wardrobe yet, so you can't reference specific pieces "
            "they own. In 3-4 sentences, describe the vibe of this item and give general "
            "styling guidance: what kinds of pieces (by type/color) pair well with it and "
            "what overall look it suits. Be concrete and friendly, not a product blurb."
        )
        system = "You are a thoughtful personal stylist who specializes in thrifted fashion."
    else:
        wardrobe_lines = "\n".join(
            f"  - {w.get('name', 'item')} "
            f"({w.get('category', '?')}; {', '.join(w.get('colors', []))})"
            for w in items
        )
        prompt = (
            f"A shopper is considering this secondhand piece:\n  {item_summary}\n\n"
            f"Here is their current wardrobe:\n{wardrobe_lines}\n\n"
            "Suggest 1-2 complete outfits that combine the NEW item with SPECIFIC pieces "
            "named from their wardrobe above (reference them by name). For each outfit give "
            "a one-line styling tip (how to wear it). Keep it to 4-6 sentences total, warm "
            "and specific — not a product description."
        )
        system = "You are a thoughtful personal stylist who specializes in thrifted fashion."

    try:
        return _chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            temperature=0.7,
        )
    except Exception as exc:  # network / API hiccup — degrade gracefully.
        return (
            f"(Styling service is unavailable right now: {exc}. "
            f"In the meantime, {new_item.get('title', 'this piece')} pairs well with "
            "simple basics in a complementary color.)"
        )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.
    """
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card without an outfit suggestion — "
            "run suggest_outfit first."
        )

    title = (new_item or {}).get("title", "this thrifted piece")
    price = (new_item or {}).get("price", "?")
    platform = (new_item or {}).get("platform", "a resale app")

    prompt = (
        f"Write a short, shareable Instagram/TikTok caption (2-4 sentences) for a "
        f"thrifted outfit.\n\n"
        f"Item: {title}\nPrice: ${price}\nPlatform: {platform}\n"
        f"Outfit/styling: {outfit}\n\n"
        "Rules:\n"
        "- Sound like a real person posting their fit, NOT a product description.\n"
        "- Mention the item name, the price, and the platform naturally (once each).\n"
        "- Capture the vibe of the outfit in specific words.\n"
        "- Casual tone, an emoji or two is fine. No hashtable dump.\n"
        "Return only the caption text."
    )

    try:
        # Higher temperature so different inputs (and reruns) read differently.
        return _chat(
            [{"role": "system", "content": "You write punchy, authentic thrift-haul captions."},
             {"role": "user", "content": prompt}],
            temperature=1.0,
            max_tokens=180,
        )
    except Exception as exc:
        return (
            f"(Couldn't generate a caption right now: {exc}. "
            f"Quick version: thrifted {title} for ${price} on {platform} — obsessed.)"
        )
