"""ITrace (plan/10 §1.2): write-only run records, kept on our side.

The environment group's runner keeps observations, not our state, so this side writes its own:
  JsonlTrace(root)   <root>/<interaction_id>/<character_id>/phases.jsonl      one PhaseLog per
                                                                              committed phase
                     <root>/<interaction_id>/<character_id>/model_calls.jsonl  one entry per
                                                                              model call (its
                                                                              view, raw output,
                                                                              reasoning)
  MemoryTrace()      the same records in memory, for tests and callers that write elsewhere
A trace never reads back and has no write right on state. Model reasoning is kept here only; it
never enters another prompt.
"""
import json
from pathlib import Path

from ..interfaces import InterfaceError
from ..stage.model import plain, view_payload


def phase_entry(key, log):
    return {"interaction_id": key[0], "character_id": key[1], **plain(log)}


def call_entry(key, call):
    return {"interaction_id": key[0], "character_id": key[1], "stage": call.stage, "input": view_payload(call.input),
            "raw_output": call.raw_output, "reasoning": call.reasoning}


class MemoryTrace:
    """ITrace in memory."""

    def __init__(self):
        self.phases, self.calls = [], []

    def phase(self, key, log):
        self.phases.append(phase_entry(key, log))

    def model_call(self, key, call):
        self.calls.append(call_entry(key, call))


def _segment(value, where):
    if not isinstance(value, str) or not value or value in (".", "..") or any(c in value for c in "/\\\0"):
        raise InterfaceError(f"{where} {value!r} cannot name a trace folder")
    return value


class JsonlTrace:
    """ITrace as JSON lines, one folder per interaction and character."""

    def __init__(self, root):
        self.root = Path(root)

    def folder(self, key):
        path = self.root / _segment(key[0], "interaction_id") / _segment(key[1], "character_id")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _append(self, key, name, entry):
        with open(self.folder(key) / name, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def phase(self, key, log):
        self._append(key, "phases.jsonl", phase_entry(key, log))

    def model_call(self, key, call):
        self._append(key, "model_calls.jsonl", call_entry(key, call))
