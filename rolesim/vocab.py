"""Shared vocabularies: interaction moves a role may choose and perceptual cues it may report."""

ACTION_TEXT = {
    "request_evidence": "向对方索要可核对的记录或证据",
    "ask_clarification": "就对方的话提问、澄清",
    "propose_plan": "提出一个具体的安排或方案",
    "set_boundary": "说明自己能承诺什么、不能承诺什么",
    "wait": "暂不表态，说明需要时间考虑或核对",
    "agree": "接受对方刚提出的安排",
    "close": "结束这次沟通",
}
ACTIONS = set(ACTION_TEXT)

CUE_TEXT = {
    "uncertainty": "信息不明、无法判断",
    "pressure": "被催促，或时间、后果上的压力",
    "commitment": "涉及承诺、约定或其兑现",
    "repair": "对方在修补关系或提出补救",
    "conflict": "双方立场或要求相冲突",
}
CUES = set(CUE_TEXT)
