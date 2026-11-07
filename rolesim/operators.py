"""IUpdateOperator (plan/10 §4.1): the 11 update operators as drafting steps.

An operator turns the proposals for constructs on its write list into StateChange drafts:
`before` from the committed snapshot, evidence and decision processes from the proposal or
from the judgments handed to this operator, the band from IMagnitude (numeric banded fields),
and the dependency group from the registry. It writes nothing; IStateStore validates and
commits. Duplicates on one address merge when they agree and are both rejected when they do
not (状态变化规则 §六: "仍无法裁决时拒绝该构念本阶段的变化，保持原值").

What makes the 11 operators different is data: their write lists, stage permissions and groups
come from the registry. Rule-card specifics that need judgment about events (direction of
emotion from appraisal, habituation, decay by elapsed time) are not computed here; the stage
proposes the value and the validator checks it.
"""
import math

from .interfaces import STAGE_OF_KIND, RejectedChange, StateChange, Violation
from .slots import AddressError, band_scale, get, instance_after, resolve


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Operator:
    """One registered update operator (IUpdateOperator)."""

    def __init__(self, rule, registry, magnitude):
        self.name = rule.name
        self.rule = rule
        self.registry = registry
        self.magnitude = magnitude

    def _support(self, proposal, judgments):
        handed = [j for j in judgments if self.name in j.hands_to]
        if proposal.mechanisms:
            handed = [j for j in judgments if j.dm_id in proposal.mechanisms]
        mechanisms = tuple(proposal.mechanisms) or tuple(dict.fromkeys(j.dm_id for j in handed))
        evidence = tuple(proposal.evidence) or tuple(dict.fromkeys(e for j in handed for e in j.evidence))
        return evidence, mechanisms

    def propose(self, ctx, proposals, judgments):
        stage = STAGE_OF_KIND[ctx.phase.kind]
        drafts, rejected, conflicts = {}, [], set()

        def reject(proposal, code, detail=""):
            rejected.append(RejectedChange(proposal, self.rule.group, Violation("hard", code, detail)))

        for p in proposals:
            if p.construct_id not in self.rule.writes:
                reject(p, "not_in_whitelist", f"{self.name} may not write {p.construct_id}")
                continue
            try:
                slot, _, _ = resolve(p.construct_id, p.key, p.field)
                before = get(ctx.state, p.construct_id, p.key, p.field)
            except AddressError as exc:
                reject(p, "bad_address", str(exc))
                continue
            evidence, mechanisms = self._support(p, judgments)
            if not evidence:
                reject(p, "no_evidence", "no evidence on the proposal or on judgments handed to this operator")
                continue
            band = None
            numeric = band_scale(slot, p.field) is not None and _number(before) and _number(p.after)
            if numeric and self.name != "decay_or_recovery" and p.grade in (1, 2, 3, 4) and p.after != before:
                construct = self.registry.construct(p.construct_id)
                direction = 1 if p.after > before else -1
                instance = instance_after(ctx.state, p.construct_id, p.key, p.field, p.after)
                band = self.magnitude.range(construct, p.field, p.grade, direction, evidence, ctx.stable, instance)
            change = StateChange(p.construct_id, p.key, p.field, before, p.after, evidence, self.name, mechanisms,
                                 stage, band, self.rule.group, p.reason, grade=p.grade)
            address = (p.construct_id, p.key, p.field)
            if address in conflicts:
                reject(p, "conflict_unresolved", f"{address} proposed with different values")
            elif address not in drafts:
                drafts[address] = change
            elif drafts[address].after == change.after:
                first = drafts[address]
                drafts[address] = StateChange(
                    first.construct_id, first.key, first.field, first.before, first.after,
                    tuple(dict.fromkeys(first.evidence + change.evidence)), self.name,
                    tuple(dict.fromkeys(first.decision_mechanisms + change.decision_mechanisms)), stage,
                    first.band, first.group, first.reason, grade=first.grade)
            else:
                conflicts.add(address)
                rejected.append(RejectedChange(drafts.pop(address), self.rule.group,
                                               Violation("hard", "conflict_unresolved", f"{address} proposed with different values")))
                reject(p, "conflict_unresolved", f"{address} proposed with different values")
        return list(drafts.values()), rejected


def operators(registry, magnitude):
    return {name: Operator(registry.operator(name), registry, magnitude) for name in registry.ids("operator")}
