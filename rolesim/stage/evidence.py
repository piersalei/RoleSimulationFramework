"""IEvidenceGate (docs/14 §3.1): what one stage of one role may treat as evidence.

状态变化规则v1 §3.1: at the start of each phase, build the role's visible evidence set and write
down what was excluded. Here:

  included   records this role acted in or was addressed by, from `ctx.history` and
             `ctx.records` (each once): "own_action" for its own, "witnessed" otherwise. Whether
             a record's content is hearsay is S1's call from the text (evidence_notes).
  excluded   another role's thought ("other_thought"); this role's own thought, a process
             record that is not evidence ("own_thought"); anything it was not addressed by
             ("not_recipient").
  retrieved  up to k memory entries (m.entry) by bigram overlap with what was just seen,
             weighted by importance and accessibility (memory.search).

Other roles' hidden state and scene-private data never reach this side, so there is nothing to
filter for them; the runner's observations are all this gate ever reads.
"""
from ..agent.agent_api import record_id, sees
from ..interfaces import EvidenceRef, EvidenceSet, Excluded
from .memory import RECALL_K, search


class EvidenceGate:
    """IEvidenceGate over the records in the stage context and the role's own memory."""

    def __init__(self, k=RECALL_K):
        self.k = k

    def build(self, ctx):
        me = ctx.key[1]
        included, excluded, seen = [], [], set()
        for record in (*ctx.history, *ctx.records):
            rid = record_id(record)
            if rid in seen:
                continue
            seen.add(rid)
            actor = record["actor"]["actor_id"]
            if not sees(record, me):
                excluded.append(Excluded(rid, "not_recipient"))
            elif record["kind"] == "thought":
                excluded.append(Excluded(rid, "own_thought" if actor == me else "other_thought"))
            else:
                included.append(EvidenceRef(rid, "own_action" if actor == me else "witnessed"))
        return EvidenceSet(tuple(included), tuple(excluded), tuple(self._recall(ctx)))

    def _recall(self, ctx):
        entries = ctx.state.memory.get("m.entry") or {}
        if not entries or not ctx.records:
            return []
        access = ctx.state.memory.get("m.accessibility") or {}
        pool = [{"id": entry_id, "content": entry.get("summary", ""),
                 "cues": {"weight": float(entry.get("importance") or 0) * float(access.get(entry_id, 1.0))}}
                for entry_id, entry in entries.items()]
        query = " ".join(r["content"] for r in ctx.records if r["kind"] != "thought")
        picked = [item["id"] for item in search(pool, query, self.k)]
        return [{"entry_id": entry_id, "summary": entries[entry_id].get("summary", ""),
                 "channel": entries[entry_id].get("channel"), "source_events": list(entries[entry_id].get("source_events", []))}
                for entry_id in picked]
