# FitFindr — planning.md

> Completed before writing implementation code. This spec is what I used to direct
> the AI tool (Claude) when generating each tool and the planning loop.
> Updated before starting the stretch feature (retry-with-fallback — see below).

---

## What FitFindr Does (2–3 sentence summary)

FitFindr is a multi-tool agent that takes one natural-language thrifting request
("vintage graphic tee under $30, size M") and walks it through three stages:
it **searches** the mock listings dataset for matches, **suggests** how to style the
top find against the user's existing wardrobe, and **writes** a shareable fit-card
caption for it. The planning loop is responsive, not fixed: if the search returns
nothing it first retries with the size filter loosened, and only if that still
returns nothing does it stop early with a helpful message — it never calls
`suggest_outfit` or `create_fit_card` on empty input.

---

## Tools

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset and returns the listings that match the
user's keywords, ranked by relevance, after applying optional size and price filters.

**Input parameters:**
- `description` (str): Free-text keywords describing the wanted item, e.g. `"vintage graphic tee"`. Tokenized and scored against each listing's title, description, style_tags, category, colors, and brand.
- `size` (str | None): Size to filter by, e.g. `"M"` or `"8"`. Case-insensitive token match against the listing's `size` field (so `"M"` matches `"S/M"`). `None` skips size filtering.
- `max_price` (float | None): Inclusive price ceiling. `None` skips price filtering.

**What it returns:**
A `list[dict]` of full listing dicts (`id`, `title`, `description`, `category`,
`style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by
relevance score (highest first). Listings scoring 0 keyword overlap are dropped.
Returns `[]` (empty list, never an exception) when nothing matches.

**What happens if it fails or returns nothing:**
Returns `[]`. The planning loop — not the tool — decides what to do: it first retries
without the size filter, and if that is still empty it sets `session["error"]` and
stops before any LLM tool runs.

---

### Tool 2: suggest_outfit

**What it does:**
Given the selected listing and the user's wardrobe, asks the LLM to propose 1–2
complete outfits that pair the new item with specific, named wardrobe pieces.

**Input parameters:**
- `new_item` (dict): The listing dict the user is considering buying (the top search result).
- `wardrobe` (dict): A wardrobe in the schema's `{"items": [...]}` format. May be empty.

**What it returns:**
A non-empty `str` of styling advice. When the wardrobe has items, it names real pieces
("pair with your baggy dark-wash jeans and chunky white sneakers"). When the wardrobe
is empty, it returns general styling guidance for the item instead.

**What happens if it fails or returns nothing:**
The empty-wardrobe branch is detected up front (`not wardrobe.get("items")`) and routed
to a general-advice prompt rather than crashing. Any LLM/network exception is caught and
returned as a readable fallback string so the agent stays usable.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion plus the item details into a short, casual,
Instagram-caption-style "fit card."

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit`.
- `new_item` (dict): The listing dict, used for the item name, price, and platform.

**What it returns:**
A 2–4 sentence `str` caption that mentions the item, price, and platform naturally and
captures the vibe. Generated at a higher temperature so repeated/different inputs vary.

**What happens if it fails or returns nothing:**
If `outfit` is empty/whitespace it returns a descriptive error string (no exception). LLM
exceptions are caught and returned as a readable fallback string.

---

### Additional Tools

None required. The **retry-with-fallback** stretch is implemented inside the planning
loop (it re-invokes `search_listings`), not as a separate tool.

---

## Planning Loop

The loop is a linear pipeline with one conditional retry branch and one early-exit branch.

1. `_new_session()` creates the session dict (single source of truth).
2. **Parse** the query with regex into `{description, size, max_price}` → `session["parsed"]`.
   Regex is used (not the LLM) because the parse targets are simple and deterministic:
   - price: `(?:under|below|less than|max|<)\s*\$?\s*(\d+...)` then fall back to `\$\s*(\d+...)`
   - size: `size\s+([A-Za-z0-9/.]+)` then a word-boundary scan for `XXS|XS|S|M|L|XL|XXL`
   - description: the query with the matched price/size phrases stripped out.
3. **search_listings(description, size, max_price)** → `session["search_results"]`.
4. **Branch — empty results:**
   - If empty **and a size was specified**, retry `search_listings` with `size=None`,
     record the loosened filter in `session["adjustments"]`, and use those results.
   - If still empty (or no size to loosen), set `session["error"]` with a specific message
     and **return early** — `suggest_outfit`/`create_fit_card` are never called.
5. `session["selected_item"] = search_results[0]` (top-ranked match).
6. **suggest_outfit(selected_item, wardrobe)** → `session["outfit_suggestion"]`.
7. **create_fit_card(outfit_suggestion, selected_item)** → `session["fit_card"]`.
8. Return the session.

Behavior changes with input: an impossible query stops at step 4; a query whose only
problem is the size filter recovers at step 4's retry and continues; a normal query runs
all three tools. The agent never runs a fixed all-three sequence regardless of results.

---

## State Management

A single `session` dict (created by `_new_session`) is the single source of truth and is
threaded through the whole interaction. Each step **writes** its output to a named key and
later steps **read** from it — nothing is re-entered by the user or hardcoded.

| Key | Written by | Read by |
|-----|-----------|---------|
| `query` | `_new_session` | parse step |
| `parsed` (`description/size/max_price`) | parse step | `search_listings` call |
| `search_results` | `search_listings` | empty-check / selection |
| `adjustments` | retry branch | UI (to tell the user what changed) |
| `selected_item` | selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` (from caller) | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any early-exit branch | UI (checked first) |

The exact dict in `selected_item` is the same object passed into both `suggest_outfit` and
`create_fit_card` — that is how the item found by search flows downstream without re-entry.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Loop first retries with the size filter dropped and tells the user ("No size-M matches, so I searched all sizes…"). If still empty, returns: "No listings matched '<desc>' under $<price>. Try removing the price cap, using broader keywords (e.g. 'tee' instead of 'vintage graphic tee'), or a different style." No downstream tools run. |
| suggest_outfit | Wardrobe is empty | Detects empty `items` up front and returns general styling advice for the item ("you don't have a wardrobe saved yet, so here's how this piece reads and what to pair it with…") instead of crashing or returning "". |
| create_fit_card | Outfit input missing/incomplete | Returns a descriptive string: "Can't write a fit card without an outfit suggestion — run suggest_outfit first." No exception. |
| (all LLM tools) | Groq API / network error | `try/except` returns a readable fallback string so a backend hiccup degrades gracefully instead of crashing the agent. |

---

## Architecture

```
User query + wardrobe choice
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Planning Loop (run_agent)                         session (state)     │
│                                                    ┌─────────────────┐ │
│  parse query (regex) ─────────────────────────────►│ parsed          │ │
│        │                                           │                 │ │
│        ▼                                           │                 │ │
│  search_listings(description,size,max_price) ─────►│ search_results  │ │
│        │                                           │                 │ │
│        ├─ results == [] and size set?              │                 │ │
│        │     └─► retry search_listings(size=None) ►│ adjustments     │ │
│        │                                           │                 │ │
│        ├─ still [] ──► [ERROR] set session.error ──► return early ────┼─┐
│        │                                           │                 │ │
│        ▼ results=[item,...]                        │                 │ │
│  selected_item = results[0] ──────────────────────►│ selected_item   │ │
│        │                                           │                 │ │
│        ▼                                           │                 │ │
│  suggest_outfit(selected_item, wardrobe) ─────────►│ outfit_suggestion│ │
│        │                                           │                 │ │
│        ▼                                           │                 │ │
│  create_fit_card(outfit_suggestion, selected_item)►│ fit_card        │ │
│        │                                           └─────────────────┘ │
│        ▼                                                                │
│   return session ◄──────────────────────────────────── error path ────┼─┘
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
  Gradio UI: listing panel | outfit panel | fit-card panel
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations (AI tool: Claude):**
- `search_listings`: Give Claude the Tool 1 block above (inputs, return value, failure mode)
  plus the listing field list, and ask it to implement filtering + keyword scoring using
  `load_listings()`. **Verify before trusting:** confirm it filters by all three params,
  drops zero-score listings, sorts by score, and returns `[]` (not `None`/exception) for the
  impossible query. Test with: a normal query, a price-only query, and the ballgown query.
- `suggest_outfit` / `create_fit_card`: Give Claude each tool's block and ask it to call
  Groq `llama-3.3-70b-versatile`. **Verify:** empty wardrobe returns general advice (no crash);
  `create_fit_card("")` returns an error string; the caption varies across runs (raise
  temperature if not).

**Milestone 4 — Planning loop and state management (AI tool: Claude):**
- Give Claude the **Architecture diagram** + **Planning Loop** + **State Management** sections
  and ask it to implement `run_agent`. **Verify:** it branches on the search result (does not
  call all three tools unconditionally), writes each result into the session dict, performs the
  size-loosen retry, and returns early with `session["error"]` set on the no-results path.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy
jeans and chunky sneakers. What's out there and how would I style it?" (Example wardrobe.)

**Step 1 — Parse.** Regex extracts `max_price = 30.0`, no explicit size, and
`description = "vintage graphic tee"` (price/size phrases stripped). Stored in `session["parsed"]`.

**Step 2 — Search.** `search_listings("vintage graphic tee", size=None, max_price=30.0)` scores
the catalog; "graphic tee"/"vintage" hits rank items like *Graphic Tee — 2003 Tour Bootleg Style
($24, depop)* and *Vintage Band Tee — Faded Grey ($19, depop)* at the top. Results stored;
`selected_item = results[0]`.

**Step 3 — Suggest outfit.** `suggest_outfit(selected_item, example_wardrobe)` returns specific
advice, e.g. "Pair it with your baggy dark-wash jeans and chunky white sneakers; throw the vintage
black denim jacket over it for a 90s grunge look." Stored in `session["outfit_suggestion"]`.

**Step 4 — Fit card.** `create_fit_card(outfit_suggestion, selected_item)` returns something like
"scored this bootleg tour tee off depop for $24 and it was MADE for my baggy jeans 🖤 grunge szn."
Stored in `session["fit_card"]`.

**Final output to user:** three panels — the top listing (title/price/platform/condition), the
outfit suggestion, and the fit-card caption.
