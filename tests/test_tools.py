"""
Tests for the three FitFindr tools.

Covers the happy path and each tool's documented failure mode. The LLM-backed
tools (suggest_outfit, create_fit_card) make real Groq calls, so those tests
need GROQ_API_KEY set and are skipped automatically if it is missing.

Run with:  pytest tests/
"""

import os
import sys

import pytest

# Make the project root importable when pytest is run from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

HAS_KEY = bool(os.environ.get("GROQ_API_KEY"))
needs_llm = pytest.mark.skipif(not HAS_KEY, reason="GROQ_API_KEY not set")


# ── search_listings ─────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible query → empty list, no exception (the documented failure mode).
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=40)
    assert all(item["price"] <= 40 for item in results)


def test_search_size_filter_token_match():
    # "M" should match listings whose size contains M (e.g. "M", "M/L", "S/M").
    results = search_listings("top", size="M", max_price=None)
    assert results, "expected at least one M-ish top"
    assert all("M" in item["size"].upper() for item in results)


def test_search_results_sorted_by_relevance():
    # A multi-keyword query should rank a true graphic/band tee at the top.
    results = search_listings("vintage band tee", size=None, max_price=None)
    assert results
    top_tags = {t.lower() for t in results[0]["style_tags"]}
    assert {"band tee", "graphic tee", "vintage"} & top_tags


# ── suggest_outfit ──────────────────────────────────────────────────────────

@needs_llm
def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and out.strip()


@needs_llm
def test_suggest_outfit_empty_wardrobe():
    # Empty wardrobe must return useful advice, not crash or return "".
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and len(out.strip()) > 0


# ── create_fit_card ─────────────────────────────────────────────────────────

def test_fit_card_empty_outfit_returns_error_string():
    # No LLM call happens here — the guard fires first.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    msg = create_fit_card("", item)
    assert isinstance(msg, str)
    assert "suggest_outfit" in msg  # the descriptive guard message


@needs_llm
def test_fit_card_varies_for_same_input():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    outfit = "Pair with baggy jeans and chunky sneakers."
    a = create_fit_card(outfit, item)
    b = create_fit_card(outfit, item)
    assert a.strip() and b.strip()
    assert a != b  # higher temperature → different captions
