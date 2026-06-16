# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand pieces and figure out how to wear
them. Give it one natural-language request — *"vintage graphic tee under $30, size M"* —
and it **searches** the mock listings, **suggests** an outfit using your existing wardrobe,
and **writes** a shareable fit-card caption for the find.

The agent is responsive, not scripted: it branches on what each tool returns, retries a
search with the size filter loosened before giving up, and stops cleanly (without ever
calling a downstream tool on empty input) when there's genuinely nothing to show.

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate          # Windows (Git Bash);  .venv\Scripts\activate on cmd
pip install -r requirements.txt
```

Create a `.env` in the repo root (it's gitignored — never commit it):

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

### Run the app

```bash
python app.py
```

Open the localhost URL printed in your terminal (usually <http://localhost:7860>).

### Run the tests

```bash
pytest tests/
```

The LLM-backed tests call Groq and are skipped automatically if `GROQ_API_KEY` is unset;
the search and guard tests run with no key.

---

## Project layout

```
agent.py          # Planning loop + query parsing + session state (run_agent)
tools.py          # The three tools (search_listings, suggest_outfit, create_fit_card)
app.py            # Gradio UI + handle_query mapping the session to three panels
tests/test_tools.py
data/             # listings.json (40 listings), wardrobe_schema.json
utils/data_loader.py
planning.md       # Spec written before implementation
```

The LLM tools use Groq's `llama-3.3-70b-versatile`.

---

## Tool inventory

### 1. `search_listings(description, size, max_price) -> list[dict]`
**Inputs**
- `description` (str) — keywords describing the item, e.g. `"vintage graphic tee"`.
- `size` (str | None) — size to filter by, e.g. `"M"`; `None` skips size filtering.
- `max_price` (float | None) — inclusive price ceiling; `None` skips price filtering.

**Output** — a `list[dict]` of full listing dicts (`id`, `title`, `description`, `category`,
`style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by
relevance (best first). Returns `[]` when nothing matches.

**Purpose** — filters the catalog by price and size, then scores each remaining listing by
keyword overlap against its title/description/tags/category/colors/brand (exact style-tag
hits weigh extra), drops zero-score listings, and ranks the rest.

### 2. `suggest_outfit(new_item, wardrobe) -> str`
**Inputs**
- `new_item` (dict) — the selected listing dict.
- `wardrobe` (dict) — a wardrobe in `{"items": [...]}` form; may be empty.

**Output** — a non-empty `str` of styling advice. With a wardrobe it names real pieces the
user owns; with an empty wardrobe it gives general styling guidance for the item.

**Purpose** — turns a found item + the user's closet into concrete outfit ideas via the LLM.

### 3. `create_fit_card(outfit, new_item) -> str`
**Inputs**
- `outfit` (str) — the suggestion returned by `suggest_outfit`.
- `new_item` (dict) — the listing dict (for item name, price, platform).

**Output** — a 2–4 sentence `str` caption in a casual OOTD-post voice. Generated at a high
temperature, so it varies for the same input and differs across inputs.

**Purpose** — produces the shareable "fit card" that captions the find.

The documented inputs/outputs above match the actual function signatures in
[tools.py](tools.py).

---

## How the planning loop works

`run_agent(query, wardrobe)` in [agent.py](agent.py) is a linear pipeline with **one
conditional retry branch and one early-exit branch** — it does *not* call all three tools
unconditionally.

1. **Guard** — empty query → set `error`, return.
2. **Parse** — regex extracts `description`, `size`, `max_price` from the query.
   Regex (not the LLM) because the targets are simple and deterministic.
3. **Search** — `search_listings(description, size, max_price)`.
4. **Branch on the result:**
   - **Retry:** if results are empty *and a size was specified*, re-run the search with
     `size=None` and record a note in `session["adjustments"]` ("No 'M' matches, so I searched
     all sizes"). This is the **retry-with-fallback stretch feature**.
   - **Early exit:** if results are *still* empty, set a specific `session["error"]` and
     **return immediately** — `suggest_outfit` and `create_fit_card` are never reached.
5. **Select** — `selected_item = results[0]`.
6. **Suggest** — `suggest_outfit(selected_item, wardrobe)`.
7. **Fit card** — `create_fit_card(outfit_suggestion, selected_item)`.

The agent's behavior visibly differs by input: an impossible query stops at step 4; a query
whose only problem is the size filter recovers via the step-4 retry and continues; a normal
query runs the full chain.

---

## State management

A single `session` dict (built by `_new_session`) is the source of truth for one
interaction. Each step **writes** its result to a named key; later steps **read** from it —
nothing is re-entered by the user or hardcoded between steps.

| Key | Written by | Read by |
|-----|-----------|---------|
| `query` | `_new_session` | parse step |
| `parsed` (description/size/max_price) | parse step | `search_listings` call |
| `search_results` | search (+ retry) | empty-check / selection |
| `adjustments` | retry branch | UI (tells the user what changed) |
| `selected_item` | selection step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | caller via `_new_session` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any early-exit branch | UI (checked first) |

The exact dict stored in `selected_item` is the same object passed into both `suggest_outfit`
and `create_fit_card` — that's how the item found by search flows downstream with no re-entry.
`app.py` reads only from this session dict to populate the three UI panels.

---

## Error handling strategy (per tool)

| Tool | Failure mode | What the agent does |
|------|-------------|---------------------|
| `search_listings` | No results | Loop first **retries with the size filter dropped** and tells the user. If still empty, returns a specific, actionable message and stops — no downstream tool runs. |
| `suggest_outfit` | Empty wardrobe | Detected up front; routes to a general-advice prompt and returns useful guidance instead of crashing or returning `""`. |
| `create_fit_card` | Missing/empty outfit | Returns a descriptive string (`"Can't write a fit card without an outfit suggestion…"`); no exception. |
| both LLM tools | Groq/network error | Wrapped in `try/except`; returns a readable fallback string so a backend hiccup degrades gracefully. |
| `search_listings` | Data file missing/corrupt | `try/except` around `load_listings()` returns `[]` rather than raising. |

**Concrete example from testing** — the impossible query returns an empty list and the agent
stops before the LLM tools:

```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]

# Full agent on the same query:
Error message: No listings matched "designer ballgown" under $5. Try broader keywords
(e.g. 'tee' instead of 'vintage graphic tee'), removing the price cap, or a different style.
fit_card is None: True
```

And the retry-fallback recovering a query whose only problem was the size:

```
$ python agent.py
=== Retry-fallback path (impossible size, possible item) ===
Adjustments: ["No 'XXL' matches, so I searched all sizes — double-check the fit."]
Selected: 90s Leather Bomber — Black
```

---

## Stretch feature implemented

**Retry logic with fallback.** When `search_listings` returns nothing *and* a size was
requested, the loop automatically re-runs the search without the size filter and reports the
adjustment to the user (`session["adjustments"]`, surfaced in the listing panel). Only if the
loosened search is also empty does the agent give up. Documented in [planning.md](planning.md)
before implementation.

---

## Spec reflection

- **One way the spec helped:** writing the State Management table in `planning.md` *before*
  coding meant the session dict had every key it needed on the first pass — each tool's output
  already had a named home and a documented reader, so wiring `agent.py` was mechanical rather
  than a guessing game about what to thread where.
- **One way the implementation diverged:** the original plan treated the no-results case as a
  single early exit. While testing the size filter I realized many "no results" cases are
  really *"no results in this size"* — so I pulled the retry-with-fallback stretch forward into
  the core loop. That added a branch (and an `adjustments` field) that wasn't in the first
  diagram; I updated `planning.md` to match before shipping it.

---

## AI usage

**1. Tool keyword-scoring (search_listings).** I gave Claude the Tool 1 block from
`planning.md` (inputs, return value, failure mode) plus the listing field list, and asked it to
implement filtering + relevance scoring on top of `load_listings()`. Its first version scored
only against the title/description. I **overrode** that to also score `style_tags`,
`category`, `colors`, and `brand`, and to weight exact style-tag matches higher — otherwise a
query like "grunge" missed listings that only carried it as a tag. I also added the
empty-keyword fallback (so a bare "size M under $20" still returns something) and verified the
ballgown query returns `[]` before trusting it.

**2. Planning loop + state (run_agent).** I gave Claude the Architecture diagram and the
Planning Loop + State Management sections and asked it to implement `run_agent`. The generated
loop ran all three tools straight through; I **revised** it to add the explicit empty-results
branch with an early `return` (so the LLM tools never run on empty input) and to fold in the
size-loosen retry from my stretch spec. I verified by running all three CLI paths in
`agent.py` (happy / no-results / retry-fallback) and confirming `fit_card is None` on the
no-results path.

---

## Demo checklist (for the video)

- Full multi-step interaction on a happy-path query (e.g. *"vintage graphic tee under $30"*) —
  narrate search → suggest → fit card.
- Point at the listing panel and the outfit panel to show the *same* found item flowing into
  the outfit suggestion (state passing, no re-entry).
- Trigger the no-results query (*"designer ballgown size XXS under $5"*) and show the graceful
  error message with the other two panels empty.
- (Bonus) Show the retry-fallback note appearing for a real item in an unavailable size.
