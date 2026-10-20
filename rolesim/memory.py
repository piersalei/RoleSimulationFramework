"""Role memory: a small, local, Mem0-style store over the role's own visible items.

Like Mem0's current ADD-only pipeline, entries are appended and never rewritten;
relevance is resolved at retrieval time. Unlike Mem0 there is no LLM extraction and
no embedding: retrieval is lexical (character bigrams) plus salience, so runs stay
deterministic and standard-library only.

What a prompt sees (decision library 1.2, recall windows): the recap pinned first,
then up to `k` older entries recalled by similarity to the current event and goal,
then the most recent `window` entries. While the memory is shorter than the window,
the view is the full memory, so short episodes behave exactly as before.
"""
import copy

WINDOW = 12
RECALL_K = 4
SALIENCE_WEIGHT = .3


def add(memory, entry):
    if any(item["id"] == entry["id"] for item in memory):
        raise ValueError(f"memory already holds {entry['id']!r}; entries are add-only")
    memory.append(entry)


def _grams(value):
    chars = [c for c in str(value) if not c.isspace()]
    return {a + b for a, b in zip(chars, chars[1:])}


def salience(entry):
    return max(entry.get("cues", {}).values(), default=0)


def search(memory, query, k=RECALL_K):
    """Top-k entries by bigram overlap with the query plus salience; ties go to newer items."""
    wanted = _grams(query)
    scored = []
    for position, entry in enumerate(memory):
        overlap = len(_grams(entry["content"]) & wanted) / len(wanted) if wanted else 0
        score = overlap + SALIENCE_WEIGHT * salience(entry)
        if score > 0:
            scored.append((score, position, entry))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [entry for _, _, entry in scored[:k]]


def recall(memory, query, window=WINDOW, k=RECALL_K):
    pinned = [e for e in memory if e["type"] == "recap"]
    rest = [e for e in memory if e["type"] != "recap"]
    cut = max(0, len(rest) - window)
    older, recent = rest[:cut], rest[cut:]
    picked = {e["id"] for e in search(older, query, k)}
    recalled = [{**e, "recalled": True} for e in older if e["id"] in picked]
    return copy.deepcopy(pinned + recalled + recent)
