"""Mechanism cards (card schema 2.0): versioned engineering hypotheses, separate from source theory.

A card states three things: when it fires (`trigger`), which state it updates and how
(`effects`), and which moves it favours (`action_bias`). Every threshold, weight and
coefficient below is an uncalibrated placeholder awaiting the psychology group's rule
cards; none is a fitted value.

Trigger conditions read three sources, all from the role's own view:
  cue    the role's appraisal of the current event (0..1)
  state  a value in the role's current dynamic state
  delta  the net change of a state value since the role last decided
Paths may contain `{other}`, the role id of whoever the event came from.

An effect names a rule from RULES; the formula lives in code, its parameters on the card.
`when: "every_event"` marks a continuous process (it also decays when nothing presses),
so it runs on every observed event whether or not the card's trigger fired. Cards with
no rule yet have no effects and say so in the mechanism log.
"""
import copy
import operator

CARD_SCHEMA = "card-2.0"
STATUS = "uncalibrated_engineering_hypothesis"
TENSION = "dynamic.affect_state.emotion.intensity"
TRUST = "dynamic.relation_state.{other}.trust"
# Scales a trust change into a 0..1 trigger strength; the soft per-step limit on trust.
DELTA_SCALE = .3
NO_STATE_RULE = "no_state_rule"

CARDS = [
    {"id": "verify", "source_ref": "decision.1.5", "status": STATUS,
     "trigger": {"any_of": [{"kind": "cue", "name": "uncertainty", "op": ">=", "value": .3}]},
     "effects": [],
     "action_bias": {"actions": ["request_evidence", "ask_clarification"], "modulator": "verification"},
     "instruction": "不确定且后果重要时索取证据或澄清；已有证据时避免重复核查。"},
    {"id": "appraise", "source_ref": "decision.2.1", "status": STATUS,
     "trigger": {"any_of": [{"kind": "cue", "name": "pressure", "op": ">=", "value": .3},
                            {"kind": "state", "path": TENSION, "op": ">=", "value": .6667}]},
     "effects": [{"rule": "tension_v1", "path": TENSION, "operators": ["emotion_update", "decay_or_recovery"],
                  "group": "affect", "when": "every_event", "params": {"gain": .35}}],
     "action_bias": {"actions": ["wait", "ask_clarification"], "modulator": "threat"},
     "instruction": "区分目标威胁和对方意图；压力本身不足以证明恶意。高负荷时可暂停或缩小承诺。"},
    {"id": "trust", "source_ref": "decision.3.4", "status": STATUS,
     "trigger": {"any_of": [{"kind": "cue", "name": "commitment", "op": ">=", "value": .3},
                            {"kind": "delta", "path": TRUST, "op": "<=", "value": -.05}]},
     "effects": [{"rule": "trust_v1", "path": TRUST, "operators": ["relation_update"],
                  "group": "relation", "when": "every_event", "params": {"gain": .15}}],
     "action_bias": {"actions": ["request_evidence", "propose_plan"], "modulator": "distrust"},
     "instruction": "根据兑现证据调整对此人的可靠性判断；一次失约不直接推出恶意或能力低。"},
    {"id": "repair", "source_ref": "decision.8.5", "status": STATUS,
     "trigger": {"any_of": [{"kind": "cue", "name": "repair", "op": ">=", "value": .3}]},
     "effects": [],
     "action_bias": {"actions": ["propose_plan", "ask_clarification"], "modulator": "cooperation"},
     "instruction": "修复需要具体解释或可验证行动；可以提出小范围合作，不能把口头道歉当作兑现。"},
    {"id": "boundary", "source_ref": "decision.6.8", "status": STATUS,
     "trigger": {"any_of": [{"kind": "cue", "name": "conflict", "op": ">=", "value": .3}]},
     "effects": [],
     "action_bias": {"actions": ["set_boundary", "propose_plan"], "modulator": "directness"},
     "instruction": "明确表达可承诺与不可承诺的范围；对方提供新条件时允许协商。"},
]

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
WHEN = {"every_event", "card_triggered"}


# -- rules: formula in code, parameters on the card ---------------------------
def _get(data, path):
    for key in path.split(".")[1:]:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def _clamp(value):
    return round(min(1, max(0, value)), 4)


def tension_v1(before, inputs, params):
    return _clamp(before * (1 - inputs["recovery_rate"]) + params["gain"] * inputs["pressure"] * inputs["threat_sensitivity"])


def trust_v1(before, inputs, params):
    return _clamp(before + params["gain"] * inputs["reliability_signal"])


def _tension_inputs(ctx, effect):
    traits = ctx["stable"]["traits"]["affective_capacities"]
    pressure = ctx["cues"].get("pressure", 0)
    inputs = {"pressure": pressure, "threat_sensitivity": traits["threat_sensitivity"],
              "recovery_rate": traits["recovery_rate"]}
    mechanism = "emotion_update" if pressure else "decay_or_recovery"
    reason = ("角色感受到压力" if ctx["appraised"] else "事件带有压力线索") if pressure else "无新压力，紧张按恢复速率回落"
    return [(effect["path"], inputs, mechanism, reason)]


def _trust_inputs(ctx, effect):
    event, signals = ctx["event"], ctx["event"].signals or {}
    # Someone's words are not evidence of fulfilment: only narrated facts and
    # adjudicated outcomes may move reliability.
    if event.type not in {"narration", "outcome"} or not signals.get("reliability_signal"):
        return []
    others = ctx["others"]
    about = signals.get("about") or (others[0] if len(others) == 1 else None)
    if about not in others:
        return []
    return [(effect["path"].format(other=about), {"reliability_signal": signals["reliability_signal"]},
             "relation_update", "可核验的兑现/失约记录")]


RULES = {"tension_v1": {"apply": tension_v1, "inputs": _tension_inputs},
         "trust_v1": {"apply": trust_v1, "inputs": _trust_inputs}}


def recompute(rule, before, inputs, params):
    """Re-derive a rule's output from its logged inputs, for the attribution audit."""
    return RULES[rule]["apply"](before, inputs, params)


# -- triggers -----------------------------------------------------------------
def _observed(cond, ctx):
    if cond["kind"] == "cue":
        return ctx["cues"].get(cond["name"])
    path = cond["path"]
    if "{other}" in path:
        if ctx.get("other") is None:
            return None
        path = path.format(other=ctx["other"])
    if cond["kind"] == "state":
        value = _get(ctx["dynamic"], path)
        return value if isinstance(value, (int, float)) else None
    moved = [c["after"] - c["before"] for c in ctx.get("since", [])
             if c["path"] == path and isinstance(c["before"], (int, float)) and isinstance(c["after"], (int, float))]
    return round(sum(moved), 4) if moved else None


def matched_conditions(card, ctx):
    """The card's trigger conditions that hold now, each with the value observed."""
    out = []
    for cond in card["trigger"]["any_of"]:
        seen = _observed(cond, ctx)
        if seen is not None and OPS[cond["op"]](seen, cond["value"]):
            out.append({**cond, "observed": seen})
    return out


def _strength(cond):
    if cond["kind"] == "delta":
        return min(1, abs(cond["observed"]) / DELTA_SCALE)
    return min(1, abs(cond["observed"]))


def context(persona, dynamic, cues, others, other=None, since=(), event=None, appraised=False):
    return {"stable": persona["stable"], "dynamic": dynamic, "cues": dict(cues or {}), "others": list(others),
            "other": other, "since": list(since), "event": event, "appraised": appraised}


def triggered(ctx):
    """Cards whose trigger holds in `ctx`, for the mechanism log."""
    out = []
    for card in CARDS:
        matched = matched_conditions(card, ctx)
        if matched:
            out.append({"card_id": card["id"], "matched": matched,
                        "effects": [e["rule"] for e in card["effects"]] or NO_STATE_RULE})
    return out


def candidate_changes(ctx, evidence):
    """State changes the cards' effects produce for one observed event, before commit."""
    fired = {t["card_id"] for t in triggered(ctx)}
    changes = []
    for card in CARDS:
        for effect in card["effects"]:
            if effect["when"] == "card_triggered" and card["id"] not in fired:
                continue
            rule = RULES[effect["rule"]]
            for path, inputs, mechanism, reason in rule["inputs"](ctx, effect):
                before = _get(ctx["dynamic"], path)
                if not isinstance(before, (int, float)):
                    continue  # unknown (null) inputs skip, never a neutral guess
                after = rule["apply"](before, inputs, effect["params"])
                if after != before:
                    changes.append({"path": path, "before": before, "after": after, "evidence": [evidence],
                                    "group": effect["group"], "mechanism": mechanism, "reason": reason,
                                    "card": card["id"], "rule": effect["rule"], "when": effect["when"],
                                    "inputs": inputs})
    return changes


def select_cards(persona, ctx, allowed):
    """Up to three cards whose trigger holds now, ranked for the decision prompt."""
    s = persona["stable"]
    dynamic = ctx["dynamic"]
    tension = _get(dynamic, TENSION)
    trust = _get(dynamic, TRUST.format(other=ctx["other"])) if ctx.get("other") else None
    # An unknown (null) value contributes nothing; it is not replaced by a neutral guess.
    moderators = {"verification": s["traits"]["cognitive_style"]["verification_bias"],
                  "threat": tension if tension is not None else 0,
                  "distrust": 1 - trust if trust is not None else 0,
                  "cooperation": (s["traits"]["personality"]["facets"]["agreeableness.compliance"] + 2) / 4,
                  "directness": s["speech_style"]["baseline"]["directness"]}
    candidates = []
    for card in CARDS:
        bias = card["action_bias"]
        feasible = [a for a in bias["actions"] if a in allowed]
        matched = matched_conditions(card, ctx)
        if not matched or not feasible:
            continue
        strength = max(_strength(c) for c in matched)
        candidates.append({"id": card["id"], "source_ref": card["source_ref"], "instruction": card["instruction"],
                           "actions": feasible, "matched": matched,
                           "score": round(strength * (.5 + .3 * s["mechanism_priors"][card["id"]]
                                                      + .2 * moderators[bias["modulator"]]), 4),
                           "mapping_status": STATUS})
    return sorted(candidates, key=lambda item: (-item["score"], item["id"]))[:3]


def validate_cards(cards=CARDS):
    ids = set()
    for card in cards:
        if card["id"] in ids:
            raise ValueError(f"duplicate card {card['id']}")
        ids.add(card["id"])
        if card["status"] != STATUS or not card["trigger"]["any_of"]:
            raise ValueError(f"card {card['id']}: needs a status and at least one trigger condition")
        for cond in card["trigger"]["any_of"]:
            if cond["kind"] not in {"cue", "state", "delta"} or cond["op"] not in OPS:
                raise ValueError(f"card {card['id']}: bad trigger condition {cond}")
        for effect in card["effects"]:
            if effect["rule"] not in RULES or effect["when"] not in WHEN or not effect["operators"]:
                raise ValueError(f"card {card['id']}: bad effect {effect}")


def snapshot():
    return {"schema": CARD_SCHEMA, "cards": copy.deepcopy(CARDS)}


validate_cards()
