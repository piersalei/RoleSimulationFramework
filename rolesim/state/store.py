"""IStateStore (plan/10 §4): the only place a new AgentState comes from.

draft(ctx, output): every update operator allowed in this stage reads the judgments and the
product and returns ChangeSlots with their bands (C8: the band exists before a value is chosen).
IValueChooser (chooser.py) then gives each slot its value.

commit(ctx, changes, evidence), following 状态变化规则v1 §三 / §六 / §八:
  1. `ctx.state` must be the session's current snapshot (one shared snapshot per phase).
  2. A retry of the same (session, event, phase, kind) returns the first result; a different
     payload under the same key is refused.
  3. Unchanged values are dropped. A decay change on an address another operator also writes in
     this commit is set aside (§5.11 "本算子不再处理").
  4. IValidator checks every change (a value outside its band is a hard error: rejected, never
     clipped); the store adds the soft checks that need history or several changes:
     flip_without_new_evidence, uniform_relation_change. Slots that got no value (`unvalued`)
     count as hard errors of their group.
  5. Any hard error rejects its whole dependency group; other groups commit. Soft anomalies
     are written and flagged.
  6. Accepted changes are applied to a copy; written constructs that were unknown become active.

Snapshots handed out are copies, so callers cannot change committed states. Cumulative limits
across phases and habituation need the scene's time unit (F3) and are not checked yet.
"""
import copy
import hashlib
import math
from dataclasses import replace

from ..interfaces import STAGE_OF_KIND, AgentState, CommitResult, InterfaceError, RejectedChange, Skipped, Violation
from .magnitude import Magnitude
from .operators import operators
from .slots import SLOTS, check_layout, layer_of, put
from .validator import Validator

RELATION_DIMENSIONS = ("d.relation.trust", "d.relation.closeness", "d.relation.dependence", "d.relation.commitment")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _address(change):
    return change.construct_id, change.key, change.field


def _label(change):
    key = f"[{change.key}]" if change.key else ""
    return f"{change.construct_id}{key}" + (f".{change.field}" if change.field else "")


def _copy_state(state):
    return AgentState(state.state_id, state.prev_state_id, state.stable_snapshot_id, copy.deepcopy(dict(state.dynamic)),
                      copy.deepcopy(dict(state.memory)), dict(state.versions), dict(state.status))


class ConstructStore:
    """IStateStore keyed by construct id, with permissions and bands from the registry."""

    def __init__(self, registry, magnitude=None, validator=None):
        check_layout(registry)
        self.registry = registry
        self.magnitude = magnitude or Magnitude(registry)
        self.validator = validator or Validator(registry)
        self.operators = operators(registry, self.magnitude)
        self._current, self._accepted, self._results = {}, {}, {}

    # -- sessions --------------------------------------------------------------------------
    def open(self, key, state):
        if key in self._current:
            raise InterfaceError(f"{key}: session already open")
        missing = [cid for cid in SLOTS if cid not in (state.memory if layer_of(cid) == "memory" else state.dynamic)]
        if missing:
            raise InterfaceError(f"{key}: entry state lacks {missing}")
        self._current[key] = _copy_state(state)
        self._accepted[key], self._results[key] = [], {}

    def _session(self, key):
        if key not in self._current:
            raise InterfaceError(f"{key}: no open session")
        return self._current[key]

    def snapshot(self, key):
        return _copy_state(self._session(key))

    def history(self, key):
        self._session(key)
        return tuple(result for _, result in self._results[key].values())

    # -- draft ------------------------------------------------------------------------------
    def draft(self, ctx, output):
        """Every operator allowed in this stage proposes its slots; nothing is written."""
        self._session(ctx.key)
        stage = STAGE_OF_KIND[ctx.phase.kind]
        slots, skipped = [], []
        for name in self.registry.ids("operator"):
            if stage in self.registry.operator(name).stages:
                made, skip = self.operators[name].propose(ctx, output)
                slots += made
                skipped += skip
        return self._merge(slots, skipped)

    @staticmethod
    def _merge(slots, skipped):
        """Several slots on one address (规则 §六): same direction -> one slot, the highest grade
        and all the evidence; opposite directions or different contents -> no write this phase."""
        by_address = {}
        for slot in slots:
            by_address.setdefault(_address(slot), []).append(slot)
        out = []
        for address, group in by_address.items():
            if len(group) == 1:
                out.append(group[0])
                continue
            numeric = all(s.content is None for s in group)
            if numeric and len({s.direction for s in group}) == 1:
                top = max(group, key=lambda s: (s.grade or 0))
                evidence = tuple(dict.fromkeys(e for s in group for e in s.evidence))
                mechanisms = tuple(dict.fromkeys(m for s in group for m in s.decision_mechanisms))
                out.append(replace(top, evidence=evidence, decision_mechanisms=mechanisms))
            elif not numeric and len({repr(s.content) for s in group}) == 1:
                out.append(group[0])
            else:
                skipped.append(Skipped(group[0].update_operator, "conflict_unresolved", _label(group[0])))
        return out, skipped

    # -- commit ------------------------------------------------------------------------------
    def commit(self, ctx, changes, evidence, skipped=(), unvalued=()):
        key = ctx.key
        current = self._session(key)
        commit_key = (ctx.phase.event_id, ctx.phase.phase_no, ctx.phase.kind)
        digest = hashlib.sha256(repr((ctx.state.state_id, tuple(changes), tuple(unvalued))).encode()).hexdigest()
        if commit_key in self._results[key]:
            seen, result = self._results[key][commit_key]
            if seen != digest:
                raise InterfaceError(f"{key}: {commit_key} already committed with a different payload")
            return result
        if ctx.state.state_id != current.state_id:
            raise InterfaceError(f"{key}: context read {ctx.state.state_id}, current snapshot is {current.state_id}")

        drafts, rejected = list(changes), list(unvalued)
        drafts = [c for c in drafts if c.after != c.before]
        written = {_address(c) for c in drafts if c.update_operator != "decay_or_recovery"}
        for c in [c for c in drafts if c.update_operator == "decay_or_recovery" and _address(c) in written]:
            drafts.remove(c)
            rejected.append(RejectedChange(c, c.group, Violation("soft", "decay_superseded",
                                                                  f"{_label(c)} is written by another operator in this phase")))

        verdicts = [self.validator.check(c, current, evidence) for c in drafts]
        for i, c in enumerate(drafts):
            flip = self._flip(key, c)
            if flip:
                verdicts[i].append(flip)
        self._uniform(drafts, verdicts)

        failed = {r.group for r in rejected if r.error.kind == "hard" and r.group}
        failed |= {c.group for c, v in zip(drafts, verdicts) if any(x.kind == "hard" for x in v)}
        accepted, soft = [], []
        for c, v in zip(drafts, verdicts):
            hard = [x for x in v if x.kind == "hard"]
            if hard:
                rejected.append(RejectedChange(c, c.group, hard[0]))
            elif c.group in failed:
                rejected.append(RejectedChange(c, c.group, Violation("hard", "group_rejected",
                                                                      f"another change in {c.group} has a hard error")))
            else:
                flags = [x for x in v if x.kind == "soft"]
                accepted.append(replace(c, flag=",".join(x.code for x in flags)) if flags else c)
                soft += [Violation("soft", x.code, f"{_label(c)}: {x.detail}") for x in flags]

        state = self._apply(current, accepted, ctx) if accepted else current
        result = CommitResult(state.state_id, tuple(accepted), tuple(rejected), tuple(soft), tuple(skipped))
        self._current[key] = state
        self._accepted[key] += accepted
        self._results[key][commit_key] = (digest, result)
        return result

    def _flip(self, key, change):
        if not (_number(change.before) and _number(change.after)):
            return None
        for prev in reversed(self._accepted[key]):
            if _address(prev) == _address(change) and _number(prev.before) and _number(prev.after):
                if (prev.after - prev.before) * (change.after - change.before) < 0 and \
                        {e.event for e in change.evidence} <= {e.event for e in prev.evidence}:
                    return Violation("soft", "flip_without_new_evidence", "reverses the last change on the same evidence")
                return None
        return None

    @staticmethod
    def _uniform(drafts, verdicts):
        ratios = {}
        for i, c in enumerate(drafts):
            if c.construct_id in RELATION_DIMENSIONS and c.field == "value" and _number(c.before) and c.before > 0 \
                    and _number(c.after) and not any(x.kind == "hard" for x in verdicts[i]):
                ratios.setdefault(c.key, {})[c.construct_id] = (i, c.after / c.before)
        for partner, items in ratios.items():
            values = [r for _, r in items.values()]
            if len(items) == len(RELATION_DIMENSIONS) and max(values) - min(values) < 1e-9:
                for i, _ in items.values():
                    verdicts[i].append(Violation("soft", "uniform_relation_change",
                                                 f"all relation dimensions towards {partner} move by the same ratio"))

    @staticmethod
    def _apply(current, accepted, ctx):
        dynamic, memory = copy.deepcopy(dict(current.dynamic)), copy.deepcopy(dict(current.memory))
        status = dict(current.status)
        for c in accepted:
            put(memory if layer_of(c.construct_id) == "memory" else dynamic, c.construct_id, c.key, c.field, c.after)
            for mark in (c.construct_id, f"{c.construct_id}/{c.key}"):
                if status.get(mark) in ("unknown", "inactive"):
                    status[mark] = "active"
        state_id = f"{ctx.key[1]}@{ctx.phase.event_id}#{ctx.phase.phase_no}.{ctx.phase.kind}"
        return AgentState(state_id, current.state_id, current.stable_snapshot_id, dynamic, memory,
                          dict(current.versions), status)
