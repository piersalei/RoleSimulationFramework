"""Memory retrieval for the evidence gate (IEvidenceGate, evidence.py): which of the role's own
memory entries (m.entry) a stage is reminded of.

Lexical and deterministic, standard library only: character-bigram overlap with what was just
seen, plus a salience weight (the gate passes importance x accessibility). No LLM extraction and
no embedding. The entries themselves live in the state (m.entry, add-only through IStateStore);
this module never writes.
"""
RECALL_K = 4
SALIENCE_WEIGHT = .3


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
