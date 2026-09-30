"""Versioned engineering hypotheses, intentionally separate from source theory."""
import copy

CARDS = [
    {"id": "verify", "source_ref": "decision.1.5", "cue": "uncertainty", "threshold": .3,
     "instruction": "不确定且后果重要时索取证据或澄清；已有证据时避免重复核查。",
     "actions": ["request_evidence", "ask_clarification"], "modulator": "verification"},
    {"id": "appraise", "source_ref": "decision.2.1", "cue": "pressure", "threshold": .3,
     "instruction": "区分目标威胁和对方意图；压力本身不足以证明恶意。高负荷时可暂停或缩小承诺。",
     "actions": ["wait", "ask_clarification"], "modulator": "threat"},
    {"id": "trust", "source_ref": "decision.3.4", "cue": "commitment", "threshold": .3,
     "instruction": "根据兑现证据调整对此人的可靠性判断；一次失约不直接推出恶意或能力低。",
     "actions": ["request_evidence", "propose_plan"], "modulator": "distrust"},
    {"id": "repair", "source_ref": "decision.8.5", "cue": "repair", "threshold": .3,
     "instruction": "修复需要具体解释或可验证行动；可以提出小范围合作，不能把口头道歉当作兑现。",
     "actions": ["propose_plan", "ask_clarification"], "modulator": "cooperation"},
    {"id": "boundary", "source_ref": "decision.6.8", "cue": "conflict", "threshold": .3,
     "instruction": "明确表达可承诺与不可承诺的范围；对方提供新条件时允许协商。",
     "actions": ["set_boundary", "propose_plan"], "modulator": "directness"},
]


def select_cards(persona, state, observation, allowed):
    s = persona["stable"]
    moderators = {"verification": s["cognition"]["verification_bias"],
                  "threat": state["tension"], "distrust": 1 - state["reliability"],
                  "cooperation": (s["personality"]["facets"]["agreeableness.compliance"] + 2) / 4,
                  "directness": s["communication"]["directness"]}
    candidates = []
    for card in CARDS:
        intensity = observation["cues"].get(card["cue"], 0)
        feasible = [a for a in card["actions"] if a in allowed]
        if intensity < card["threshold"] or not feasible:
            continue
        candidate = copy.deepcopy(card)
        candidate.update(actions=feasible, trigger_event=observation["id"],
                         score=round(intensity * (.5 + .3 * s["mechanism_priors"][card["id"]]
                                                   + .2 * moderators[card["modulator"]]), 4),
                         mapping_status="uncalibrated_engineering_hypothesis")
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: (-item["score"], item["id"]))[:3]
