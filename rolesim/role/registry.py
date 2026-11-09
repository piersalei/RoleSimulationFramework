"""IRegistry (plan/10 §2.2): the psychology group's v1 definitions as read-only data.

`build_registry(source_dir)` reads the three v1 interface files and the three stage skills in
data/sources/psychology_v1/ and returns plain JSON data; `python3 -m rolesim registry` writes it
to data/registry.json. `Registry` loads that file and answers the IRegistry queries.

Extracted mechanically, from text with a regular shape:
  constructs: id, name, section, module ............. 心理构念登记 §4-6 entry lines
  decision processes: id, section, name, options ..... 心理构念登记 §7 entry lines
  operator write lists ............................... 状态变化规则 §5.1-5.10 定义与理论依据
  operator timing .................................... 状态变化规则 §5.x 时间与优先级
  stages per operator / per decision process ......... each stage skill §4.2 / §4.1
  mechanism cards and the operators they name ........ 状态变化规则 §4.1
  chapter-level operators for the other processes .... 状态变化规则 §4
Read from prose, in the tables below. Each entry quotes the phrase it rests on and the build
fails when that phrase is no longer in the source, so a new source version forces a re-read:
  dependency groups, tempo classes, time-decay rules, self-visibility, high-impact flags, and
  the few option lists the generic option parser gets wrong.

Every build also checks what the sources say about themselves (42 stable / 42 dynamic / 3
memory constructs, 75 decision processes, 11 operators; one primary writer per dynamic or
memory construct). Findings that are not errors, such as a decision process no stage calls,
go to `review`, for the psychology group to confirm.

Evidence tier: source theory plus the psychology group's engineering conventions. Their
numbers are initial values awaiting calibration; nothing here is a validated result.
"""
import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType

from ..interfaces import STAGES, Construct, DecisionMechanism, OperatorRule

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "data" / "sources" / "psychology_v1"
REGISTRY_PATH = ROOT / "data" / "registry.json"
SCHEMA_VERSION = "registry-1.0"

FILES = {"overview": "接口文件v1/接口文件总说明v1.md", "registry": "接口文件v1/心理构念登记v1.md",
         "state": "接口文件v1/角色状态说明v1.md", "rules": "接口文件v1/状态变化规则v1.md",
         "S1": "skills/事件建模skill.md", "S2": "skills/行动计划skill.md", "S3": "skills/长期演化skill.md"}
TITLES = {"overview": "接口文件总说明v1", "registry": "心理构念登记v1", "state": "角色状态说明v1",
          "rules": "状态变化规则v1", "S1": "事件建模skill", "S2": "行动计划skill", "S3": "长期演化skill"}


class RegistryBuildError(ValueError):
    """The sources no longer match what the builder, or one of its readings, expects."""


# -- readings of prose ---------------------------------------------------------
# (value, construct ids or "prefix.*" patterns, source, section, quoted phrase)

# 状态变化规则 §六 names the groups by state domain; belief_revision and other_model_update are in
# none of them ("未声明分组的变化视为同一批"). Checked against every "与 X 同组" in the rule cards.
GROUPS = {"emotion_update": "affect_self_relation_group", "self_state_update": "affect_self_relation_group",
          "relation_update": "affect_self_relation_group", "group_state_update": "affect_self_relation_group",
          "goal_update": "goal_commitment_strategy", "commitment_update": "goal_commitment_strategy",
          "strategy_learning": "goal_commitment_strategy", "memory_update": "memory",
          "decay_or_recovery": "decay", "belief_revision": "ungrouped", "other_model_update": "ungrouped"}
GROUPS_BASIS = ("rules", "§六", "本版本的依赖组为：情绪、自我、关系和群体状态；目标、承诺和策略；记忆；衰减与恢复。"
                "未声明分组的变化视为同一批。")

TEMPO = [
    ("slow", ["d.relation.trust", "d.relation.closeness", "d.relation.dependence", "d.relation.commitment",
              "d.cognition.belief", "d.cognition.shame", "d.goal.goal_commitment", "d.goal.feasibility_estimate",
              "d.policy.strategy_weight"], "rules", "§5.0",
     "慢变构念（trust、closeness、dependence、commitment、确信度、羞耻负荷、动态目标承诺、可行性估计、策略权重等 0—1 构念）"),
    ("fast", ["d.affect.emotion", "d.affect.stress", "d.affect.threat_reward_activation", "d.goal.value_activation",
              "d.goal.goal_activation", "d.group.identity_salience", "d.group.norm_salience", "d.cognition.face_cost"],
     "rules", "§5.0", "快变构念（情绪强度与唤醒、压力、威胁与奖赏激活、价值与目标激活、身份与规范显著性、面子成本）"),
    ("slow", ["d.cognition.self_evaluation"], "rules", "§5.0", "当前自我评价（与自尊基线同为 -2—+2）取慢变区间的四倍"),
    ("categorical", ["d.relation.harm_history", "d.goal.goals_now", "d.policy.action_commitment"], "rules", "§5.0",
     "类别型构念（伤害事件的修复程度、目标阶段、承诺阶段）用状态转移描述"),
]

# 状态变化规则 §九: each bullet's lead phrase -> constructs. Every bullet must be claimed, so a new
# bullet in the source fails the build. These are also decay_or_recovery's whole write list.
DECAY = [
    ("当前情绪的强度和唤醒", ["d.affect.emotion"]),
    ("注意焦点的持续度", ["d.cognition.attention"]),
    ("压力水平和威胁与奖赏激活", ["d.affect.stress", "d.affect.threat_reward_activation"]),
    ("调节状态", ["d.affect.regulation_state"]),
    ("价值激活和目标激活", ["d.goal.value_activation", "d.goal.goal_activation"]),
    ("身份显著性和规范显著性", ["d.group.identity_salience", "d.group.norm_salience"]),
    ("面子成本", ["d.cognition.face_cost"]),
    ("羞耻负荷", ["d.cognition.shame"]),
    ("当前自我评价", ["d.cognition.self_evaluation"]),
    ("伤害事件的显著性", ["d.relation.harm_history"]),
    ("记忆的可及性", ["m.accessibility"]),
    ("短期决策偏好", ["d.policy.mechanism_preference_state"]),
    ("策略权重中由短期反馈造成的偏离", ["d.policy.strategy_weight"]),
]

# Applied in order; a later entry overrides an earlier one for the same construct.
VISIBILITY = [
    ("aware", ["d.*"], "state", "§五", "dynamic 默认的可见性为：角色本人可以体验或知晓"),
    ("aware", ["s.identity.demographics"], "state", "§4.1", "角色本人完整知晓"),
    ("partial", ["s.identity.life_history"], "state", "§4.1", "角色的自知可能带有选择性和自我叙事加工"),
    ("partial", ["s.traits.personality.*"], "state", "§4.2", "角色对自己的人格只有部分自知"),
    ("partial", ["s.traits.attachment.global"], "state", "§4.3", "角色通常只能描述关系体验，不能直接说出轴的位置"),
    ("unaware", ["s.traits.motivation.conflict_regulation"], "state", "§4.3",
     "冲突主题和自体、他人表征与关系模板对角色本人不可自知"),
    ("aware", ["s.traits.motivation.self_efficacy"], "registry", "§4.6", "角色只知自我效能，不知能力分"),
    ("unaware", ["s.traits.social_cognition_71d"], "state", "§4.3", "角色本人不知道自己的能力分"),
    ("partial", ["s.traits.cognitive.*"], "registry", "§4.7", "角色部分自知；初始化以规则推断为主"),
    ("unaware", ["s.traits.development.position"], "state", "§4.3",
     "development_stage 有值时至少要有一条跨事件或具有生命史连续性的证据。角色本人不能自知"),
    ("unaware", ["s.traits.function.*"], "state", "§4.3", "剖面不典型时为“混合/未定”。角色本人不能自知"),
    ("partial", ["s.traits.decision_preferences"], "state", "§4.3", "防御偏好对角色本人不可自知，其余子项部分自知"),
    ("partial", ["s.values.*"], "state", "§4.4", "values 的三个模块角色部分自知"),
    ("aware", ["s.core_goals.primary"], "state", "§4.5", "角色本人通常知晓，他人不可见"),
    ("partial", ["s.speech_style.baseline"], "state", "§4.6", "表达基线对他人可见，角色部分自知"),
    ("partial", ["d.affect.threat_reward_activation"], "registry", "§5.1", "不是基线敏感度。部分自知"),
    ("partial", ["d.affect.regulation_state"], "registry", "§5.1", "防御通常不能自知"),
    ("partial", ["d.cognition.attention"], "registry", "§5.2", "忽略项通常不能自知"),
    ("partial", ["d.cognition.shame"], "registry", "§5.2", "也不是内疚。部分自知"),
    ("partial", ["d.policy.*"], "state", "§5.6", "角色对自己的策略选择只有部分自知"),
]

HIGH_IMPACT = [
    (["s.identity.demographics", "s.identity.social_roles", "s.identity.life_history"], "registry", "§4.1",
     "高影响字段（非档案明写时）：年龄、健康/残障条件、正式职位、法律身份、重大失落或受害经历"),
    (["s.traits.personality.*"], "registry", "§4.2", "三套全部属于高影响字段"),
    (["s.traits.attachment.global"], "registry", "§4.3", "作用层级为关系，属于高影响字段"),
    (["s.traits.motivation.*"], "registry", "§4.4", "整个模块属于高影响字段"),
    (["s.traits.affect.regulation_capacity", "s.traits.affect.empathy"], "registry", "§4.5",
     "情绪调节能力和共情属于高影响字段"),
    (["s.traits.social_cognition_71d"], "registry", "§4.6", "未赋值维度取 0 并标为 default。属于高影响字段"),
    (["s.traits.development.position"], "registry", "§4.8", "高影响字段，只能由规则或模型推断"),
    (["s.traits.self.self_esteem", "s.traits.self.identity_status"], "registry", "§4.8", "自尊与认同状态为高影响字段"),
    (["s.traits.function.*"], "registry", "§4.9", "本模块是研究编码，只能由规则或模型推断，全部为高影响字段"),
    (["s.traits.decision_preferences"], "registry", "§4.10", "防御、受侵害反应、诚实和权威服从为高影响子项"),
    (["s.values.*"], "registry", "§4.11", "道德基础理论与罗凯奇分类只作对照映射，不独立赋值。全部为高影响字段"),
    (["s.core_goals.primary"], "registry", "§4.12", "goal_state 不能反向覆盖核心目标。高影响字段"),
    (["d.relation.trust", "d.relation.commitment", "d.relation.harm_history"], "registry", "§5.4",
     "推断得到的 trust、commitment、harm_history 属高影响内容，需审核"),
    (["d.cognition.tom_level"], "state", "§8.3", "dynamic 中由推断得到的心理理论层级初值"),
]

# Segments of a §7 entry that the generic option parser splits wrongly, keyed by the exact
# segment text (between "；"). None: the segment is a note, not options. Otherwise
# (label, items) or (label, items, note). Every key must still occur in its entry.
OPTION_FIXES = {
    "dm.cog.dual_process_mode": {"按熟悉度、时间压力设切换阈值": None},
    "dm.cog.attribution_responsibility": {
        "后续解释、宽恕、补偿、责备、惩罚、边界重设、重新分工":
            ("后续", ["解释", "宽恕", "补偿", "责备", "惩罚", "边界重设", "重新分工"])},
    "dm.cog.metacognitive_monitoring": {
        "失败归于自身判断、信息缺失、情境变化、他人不合作、运气":
            ("失败归于", ["自身判断", "信息缺失", "情境变化", "他人不合作", "运气"])},
    "dm.cog.rule_switching": {"误差达阈值后修补旧规则、并行测试、重构框架":
                              ("误差达阈值后", ["修补旧规则", "并行测试", "重构框架"])},
    "dm.affect.threat_reward_policy": {
        "依 BAS、BIS、FFFS 基线接近、回避、对抗、冻结或暂停、求助或安抚、谈判":
            ("", ["接近", "回避", "对抗", "冻结或暂停", "求助或安抚", "谈判"], "依 BAS、BIS、FFFS 基线")},
    "dm.learn.social_learning": {"观察权威、成功者、相似者、受欢迎者、亲密对象、群体多数":
                                 ("观察", ["权威", "成功者", "相似者", "受欢迎者", "亲密对象", "群体多数"])},
    "dm.learn.reinforcement": {"偏重即时、长期、社会反馈、价值一致": ("偏重", ["即时", "长期", "社会反馈", "价值一致"])},
    "dm.learn.habit_goal_directed": {"习惯或目标路径主导": ("", ["习惯路径主导", "目标路径主导"])},
    "dm.choice.temporal_discounting": {"即时回报优先、平衡、未来回报优先": ("", ["即时回报优先", "平衡", "未来回报优先"])},
    "dm.choice.planned_behavior": {"态度、主观规范、知觉行为控制形成意图，可有意图与行为缺口":
                                   ("形成意图", ["态度", "主观规范", "知觉行为控制"], "可有意图与行为缺口")},
    "dm.choice.implementation_intention": {"“如果……那么……”计划": ("", ["“如果……那么……”计划"])},
    "dm.conflict.attachment_activation": {"读焦虑、回避两轴": None,
                                          "支持情绪、工具、信息、陪伴": ("支持", ["情绪", "工具", "信息", "陪伴"])},
}


# -- text helpers --------------------------------------------------------------
NUMERALS = {c: i for i, c in enumerate("一二三四五六七八九十", 1)}
HEADING = re.compile(r"^(#{2,3}) (.+)$")
ENTRY = re.compile(r"^\*\*([sdm]\.[a-z0-9_.]+) (.+?)（(.+?)）。\*\*")
DM_ENTRY = re.compile(r"^\*\*(dm\.[a-z_]+\.[a-z_]+)\*\*（(\d+\.\d+) (.+?)）：(.+)$")
DM_ID = re.compile(r"dm\.[a-z_]+\.[a-z_]+")
CONSTRUCT_REF = re.compile(r"(?<![a-z_.])([dm]\.[a-z_]+(?:\.[a-z_]+)?)")
MODULE_REF = re.compile(r"(d\.[a-z_]+) 下(?:除(.+?)外的)?([一二三四五六七八九十]+)条构念")
TICKED = re.compile(r"`([a-z_]+)`")
LABEL_WEI = re.compile(r"^([一-鿿]{1,4}?)(?:依次)?为(.+)$")
CONTEXT = re.compile(r"^([一-鿿]{1,3}?[时后])(.+)$")
OPEN, CLOSE = "（“", "）”"


def _number(text):
    """Chinese numerals 一 to 十九."""
    if text.startswith("十"):
        return 10 + (NUMERALS[text[1]] if len(text) > 1 else 0)
    if len(text) == 2 and text[1] == "十":
        return NUMERALS[text[0]] * 10
    return NUMERALS[text]


def _block(text, title_start, where):
    """The body under the first heading whose title starts with `title_start`, up to the next
    heading of the same or a higher level."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        head = HEADING.match(line)
        if head and head[2].startswith(title_start):
            body = []
            for nxt in lines[i + 1:]:
                h = HEADING.match(nxt)
                if h and len(h[1]) <= len(head[1]):
                    break
                body.append(nxt)
            return "\n".join(body)
    raise RegistryBuildError(f"{where}: heading not found: {title_start}")


def _paragraph(block, label, where):
    for para in block.split("\n\n"):
        if para.strip().startswith(f"**{label}。**"):
            return para.strip()[len(label) + 5:].strip()
    raise RegistryBuildError(f"{where}: paragraph not found: {label}")


def _split_top(text, seps):
    """Split at `seps` outside （） and “”."""
    parts, depth, buf = [], 0, []
    for ch in text:
        depth += (ch in OPEN) - (ch in CLOSE)
        if depth == 0 and ch in seps:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _partition_top(text, sep):
    parts = _split_top(text, sep)
    if len(parts) < 2 or text.strip().startswith(sep):
        return text, "", ""
    head = parts[0]
    return head, sep, text.strip()[len(head):].strip()[1:].strip()


def _ops_in(text, operators):
    return [op for op in operators if re.search(rf"(?<![a-z_]){op}(?![a-z_])", text)]


def _dedupe(items):
    return list(dict.fromkeys(items))


class _Sources:
    def __init__(self, source_dir):
        self.dir = Path(source_dir)
        self.raw = {key: (self.dir / name).read_bytes() for key, name in FILES.items()}
        self.text = {key: raw.decode("utf-8") for key, raw in self.raw.items()}

    def basis(self, key, section, phrase):
        if phrase not in self.text[key]:
            raise RegistryBuildError(f"{TITLES[key]}: phrase no longer in the source: {phrase}")
        return f"{TITLES[key]} {section}：{phrase}"

    def manifest(self):
        return [{"file": FILES[key], "sha256": hashlib.sha256(raw).hexdigest()} for key, raw in self.raw.items()]


def _expand(patterns, constructs, where):
    ids = []
    for pattern in patterns:
        if pattern.endswith(".*"):
            found = [c for c in constructs if c.startswith(pattern[:-1])]
        else:
            found = [pattern] if pattern in constructs else []
        if not found:
            raise RegistryBuildError(f"{where}: {pattern} matches no registered construct")
        ids += found
    return ids


# -- extraction ----------------------------------------------------------------
def _claimed_counts(src):
    text = src.text["overview"]
    patterns = {"stable": r"登记 (\d+) 条 stable 构念", "dynamic": r"(\d+) 条 dynamic 构念",
                "memory": r"(\d+) 条 memory 构念", "mechanisms": r"登记 (\d+) 个决策过程",
                "operators": r"只有 (\d+) 个更新算子"}
    counts = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            raise RegistryBuildError(f"{TITLES['overview']}: count not stated: {pattern}")
        counts[key] = int(match[1])
    return counts


def _constructs(src):
    out, section, module, part = [], None, None, None
    for line in src.text["registry"].splitlines():
        head = HEADING.match(line)
        if head and len(head[1]) == 2:
            part = head[2].split("、")[0]
            section, module = (str(_number(part)), "memory" if part == "六" else None) if part in NUMERALS else (None, None)
            continue
        if head:
            number, _, title = head[2].partition(" ")
            section, module = number, title.split("：")[0].strip()
            continue
        if part not in ("四", "五", "六"):
            continue
        entry = ENTRY.match(line)
        if entry:
            cid = entry[1]
            out.append({"construct_id": cid, "layer": {"s": "stable", "d": "dynamic", "m": "memory"}[cid[0]],
                        "module": module, "section": section, "name": entry[2]})
    return out


def _options(dm_id, text):
    """Option groups and leftover notes from one decision-process entry."""
    fixes = dict(OPTION_FIXES.get(dm_id, {}))
    groups, notes = [], []
    for segment in _split_top(text.rstrip("。"), "；"):
        if segment in fixes:
            fix = fixes.pop(segment)
            if fix is None:
                notes.append(segment)
            else:
                groups.append((fix[0], list(fix[1])))
                notes += list(fix[2:])
            continue
        label, body = "", segment
        head, colon, rest = _partition_top(segment, "：")
        if colon:
            label, body = head, rest
        else:
            wei = LABEL_WEI.match(segment)
            if wei and len(_split_top(wei[2], "、")) > 1:
                label, body = wei[1], wei[2]
        items = _split_top(body, "、")
        if not label and len(items) < 2:
            notes.append(segment)
            continue
        clean = []
        for item in items:
            head, comma, tail = _partition_top(item, "，")
            clean.append(head)
            if comma:
                notes.append(tail)
        context = CONTEXT.match(clean[0]) if not label else None
        if context:
            label, clean[0] = context[1], context[2]
        groups.append((label, clean))
    if fixes:
        raise RegistryBuildError(f"{dm_id}: option fix no longer matches a segment of the entry: {list(fixes)}")
    return groups, notes


def _mechanisms(src):
    out, seen = [], set()
    for line in src.text["registry"].splitlines():
        entry = DM_ENTRY.match(line)
        if not entry:
            continue
        dm_id, section, name, text = entry.groups()
        if section in seen:
            raise RegistryBuildError(f"decision library section {section} registered twice")
        seen.add(section)
        groups, notes = _options(dm_id, text)
        out.append({"dm_id": dm_id, "section": section, "name": name,
                    "options": _dedupe(i for _, items in groups for i in items),
                    "option_groups": [{"label": label, "items": items} for label, items in groups],
                    "notes": notes, "source_text": text})
    return out


def _operator_names(src):
    match = re.search(r"更新算子写回：([a-z_、]+)，", src.text["registry"])
    if not match:
        raise RegistryBuildError(f"{TITLES['registry']} §七: operator list not found")
    return match[1].split("、")


def _stage_operators(src, stage, operators):
    body = _block(src.text[stage], "4.2 更新算子", TITLES[stage])
    allowed, excluded = [], set()
    for sentence in re.split(r"(?<=。)", body):
        names = TICKED.findall(sentence)
        unknown = [n for n in names if n not in operators]
        if unknown:
            raise RegistryBuildError(f"{TITLES[stage]} §4.2: unregistered operator {unknown}")
        if "不调用" in sentence:
            excluded.update(names)
        else:
            allowed += names
    allowed = [op for op in _dedupe(allowed) if op not in excluded]
    claim = re.search(r"调用([一二三四五六七八九十]+)个更新算子", body)
    if claim and _number(claim[1]) != len(allowed):
        raise RegistryBuildError(f"{TITLES[stage]} §4.2 says {claim[1]} operators, found {allowed}")
    return allowed


def _stage_mechanisms(src, stage, by_section):
    body = _block(src.text[stage], "4.1 决策机制", TITLES[stage])
    sections = _dedupe(re.findall(r"（(\d+\.\d+)）", body))
    missing = [s for s in sections if s not in by_section]
    if missing:
        raise RegistryBuildError(f"{TITLES[stage]} §4.1 names unregistered decision-library sections {missing}")
    return [by_section[s] for s in sections]


def _rule_cards(src, operators, constructs):
    block = _block(src.text["rules"], "五、", TITLES["rules"])
    cards = re.findall(r"^### (5\.\d+) ([a-z_]+)$", block, re.M)
    if [name for _, name in cards] != operators:
        raise RegistryBuildError(f"{TITLES['rules']} §五: rule cards {[n for _, n in cards]} != operators {operators}")
    names = {c["construct_id"]: c["name"] for c in constructs}
    out = {}
    for section, name in cards:
        where = f"{TITLES['rules']} §{section}"
        body = _block(src.text["rules"], f"{section} {name}", where)
        definition = _paragraph(body, "定义与理论依据", where)
        writes = []
        for ref in MODULE_REF.finditer(definition):
            members = [cid for cid in names if cid.startswith(ref[1] + ".")]
            if ref[2]:
                kept = [cid for cid in members if names[cid] != ref[2]]
                if len(kept) != len(members) - 1:
                    raise RegistryBuildError(f"{where}: no construct named {ref[2]} under {ref[1]}")
                members = kept
            if len(members) != _number(ref[3]):
                raise RegistryBuildError(f"{where}: {ref[0]} but {len(members)} registered")
            writes += members
        for cid in CONSTRUCT_REF.findall(definition):
            if cid.startswith("d.") and cid.count(".") == 1:
                continue                     # a module reference, handled above
            if cid not in names:
                raise RegistryBuildError(f"{where}: unregistered construct {cid}")
            writes.append(cid)
        out[name] = {"section": section, "writes": _dedupe(writes),
                     "timing": _paragraph(body, "时间与优先级", where),
                     "basis": {"writes": f"{TITLES['rules']} §{section} 定义与理论依据"}}
    return out


def _decay(src, constructs):
    block = _block(src.text["rules"], "九、", TITLES["rules"])
    bullets = [line[2:].strip() for line in block.splitlines() if line.startswith("- ")]
    rules, claimed = {}, set()
    for lead, ids in DECAY:
        matched = [b for b in bullets if b.startswith(lead)]
        if len(matched) != 1:
            raise RegistryBuildError(f"{TITLES['rules']} §九: {len(matched)} bullets start with {lead}")
        claimed.add(matched[0])
        for cid in _expand(ids, constructs, "DECAY"):
            if cid in rules:
                raise RegistryBuildError(f"{cid}: two decay rules")
            rules[cid] = matched[0]
    unclaimed = [b for b in bullets if b not in claimed]
    if unclaimed:
        raise RegistryBuildError(f"{TITLES['rules']} §九: bullets no reading covers: {unclaimed}")
    return rules


def _cards_and_chapters(src, operators):
    block = _block(src.text["rules"], "四、", TITLES["rules"])
    chapters = {_number(m[1]): _ops_in(m[2], operators)
                for m in re.finditer(r"^\*\*第([一二三四五六七八九十]+)章 [^*]+?。\*\*(.+)$", block, re.M)}
    cards = {}
    for para in _block(src.text["rules"], "4.1 高影响决策过程的机制卡", TITLES["rules"]).split("\n\n"):
        para = para.strip()
        if para.startswith("**dm."):
            head = para.split("。**")[0]
            for dm_id in DM_ID.findall(head):
                cards[dm_id] = _ops_in(para, operators)
    return chapters, cards


def _check_groups(rules):
    for name, rule in rules.items():
        for match in re.finditer(r"与 ((?:[a-z_]+(?:、|和)?)+) 同组", rule["timing"]):
            for other in re.findall(r"[a-z_]+", match[1]):
                if GROUPS[other] != GROUPS[name]:
                    raise RegistryBuildError(f"{name} 时间与优先级 says same group as {other}; GROUPS disagree")


def _prior_candidates(src, mechanisms):
    """Cross-check for review: candidates in the 登记 §4.10 prior table that are not §7 options."""
    options = {m["dm_id"]: set(m["options"]) for m in mechanisms}
    block = _block(src.text["registry"], "4.10 ", TITLES["registry"])
    mismatches, current = [], []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        ids = DM_ID.findall(cells[0])
        if ids:
            current = ids
        elif cells[0]:
            continue                         # header, separator or category row
        candidate = cells[1]
        if not candidate or "连续" in candidate or not current:
            continue
        unknown = [i for i in current if i not in options]
        if unknown:
            raise RegistryBuildError(f"{TITLES['registry']} §4.10 names unregistered processes {unknown}")
        if not any(candidate in options[i] for i in current):
            mismatches.append(f"{'、'.join(current)}：{candidate}")
    return mismatches


def build_registry(source_dir=SOURCE_DIR):
    src = _Sources(source_dir)
    claimed = _claimed_counts(src)
    constructs = _constructs(src)
    by_id = {c["construct_id"]: c for c in constructs}
    if len(by_id) != len(constructs):
        raise RegistryBuildError("a construct id is registered twice")
    for layer in ("stable", "dynamic", "memory"):
        found = sum(c["layer"] == layer for c in constructs)
        if found != claimed[layer]:
            raise RegistryBuildError(f"{claimed[layer]} {layer} constructs stated, {found} found")

    operators = _operator_names(src)
    if len(operators) != claimed["operators"] or set(operators) != set(GROUPS):
        raise RegistryBuildError(f"operators {operators} do not match the stated count or GROUPS")
    rules = _rule_cards(src, operators, constructs)
    decay = _decay(src, list(by_id))
    rules["decay_or_recovery"]["writes"] = list(decay)
    rules["decay_or_recovery"]["basis"]["writes"] = f"{TITLES['rules']} §九（decay_or_recovery 可写构念以第九部分所列为限）"
    _check_groups(rules)
    group_basis = src.basis(*GROUPS_BASIS)

    writers = {}
    for name, rule in rules.items():
        if name == "decay_or_recovery":
            continue
        for cid in rule["writes"]:
            if cid in writers:
                raise RegistryBuildError(f"{cid}: written by both {writers[cid]} and {name}")
            writers[cid] = name
    unwritten = [c["construct_id"] for c in constructs if c["layer"] != "stable" and c["construct_id"] not in writers]
    if unwritten:
        raise RegistryBuildError(f"no primary writer for {unwritten}")
    if any(by_id[cid]["layer"] == "stable" for cid in writers):
        raise RegistryBuildError("an operator writes a stable construct")

    mechanisms = _mechanisms(src)
    if len(mechanisms) != claimed["mechanisms"]:
        raise RegistryBuildError(f"{claimed['mechanisms']} decision processes stated, {len(mechanisms)} found")
    by_section = {m["section"]: m["dm_id"] for m in mechanisms}
    op_stages = {op: [] for op in operators}
    dm_stages = {m["dm_id"]: [] for m in mechanisms}
    for stage in STAGES:
        for op in _stage_operators(src, stage, operators):
            op_stages[op].append(stage)
        for dm_id in _stage_mechanisms(src, stage, by_section):
            dm_stages[dm_id].append(stage)
    chapters, cards = _cards_and_chapters(src, operators)
    unknown_cards = [d for d in cards if d not in dm_stages]
    if unknown_cards:
        raise RegistryBuildError(f"mechanism cards for unregistered processes {unknown_cards}")

    attrs = {cid: {"tempo": None, "decay": decay.get(cid), "self_visibility": None, "high_impact": False,
                   "basis": {}} for cid in by_id}
    for cid in decay:
        attrs[cid]["basis"]["decay"] = f"{TITLES['rules']} §九"
    for cid, name in writers.items():
        attrs[cid]["basis"]["writer"] = rules[name]["basis"]["writes"]
    for value, patterns, key, section, phrase in TEMPO:
        basis = src.basis(key, section, phrase)
        for cid in _expand(patterns, by_id, "TEMPO"):
            if attrs[cid]["tempo"] not in (None, value):
                raise RegistryBuildError(f"{cid}: two tempo classes")
            attrs[cid].update(tempo=value)
            attrs[cid]["basis"]["tempo"] = basis
    for value, patterns, key, section, phrase in VISIBILITY:
        basis = src.basis(key, section, phrase)
        for cid in _expand(patterns, by_id, "VISIBILITY"):
            attrs[cid]["self_visibility"] = value
            attrs[cid]["basis"]["self_visibility"] = basis
    for patterns, key, section, phrase in HIGH_IMPACT:
        basis = src.basis(key, section, phrase)
        for cid in _expand(patterns, by_id, "HIGH_IMPACT"):
            attrs[cid]["high_impact"] = True
            attrs[cid]["basis"]["high_impact"] = basis

    construct_rows = [{**c, "writer": writers.get(c["construct_id"]), **attrs[c["construct_id"]]} for c in constructs]
    mechanism_rows = []
    for m in mechanisms:
        chapter = int(m["section"].split(".")[0])
        card = m["dm_id"] in cards
        stages = dm_stages[m["dm_id"]]
        basis = {"options": f"{TITLES['registry']} §7：{m['source_text']}",
                 "hands_to": (f"{TITLES['rules']} §4.1 机制卡" if card
                              else f"{TITLES['rules']} §4 第{chapter}章（按章，未细分到单个过程）")}
        if stages:
            basis["stages"] = "；".join(f"{TITLES[s]} §4.1" for s in stages)
        mechanism_rows.append({"dm_id": m["dm_id"], "section": m["section"], "name": m["name"],
                               "options": m["options"], "option_groups": m["option_groups"],
                               "stages": stages, "hands_to": cards[m["dm_id"]] if card else chapters.get(chapter, []),
                               "card": card, "notes": m["notes"], "basis": basis})
    operator_rows = [{"name": name, "section": rules[name]["section"], "writes": rules[name]["writes"],
                      "stages": op_stages[name], "group": GROUPS[name], "timing": rules[name]["timing"],
                      "basis": {**rules[name]["basis"], "group": group_basis,
                                "stages": "；".join(f"{TITLES[s]} §4.2" for s in op_stages[name]) or "无"}}
                     for name in operators]

    review = [
        {"topic": "没有任何阶段调用的决策过程（三个 skill 的 §4.1 都未列出）",
         "items": [f"{m['dm_id']}（{m['section']} {m['name']}）" for m in mechanism_rows if not m["stages"]]},
        {"topic": "未归入快变 / 慢变 / 类别的 dynamic 构念（状态变化规则 §5.0）",
         "items": [c["construct_id"] for c in construct_rows if c["layer"] == "dynamic" and c["tempo"] is None]},
        {"topic": "来源没有写明自知程度的构念（使用时按不可自知处理）",
         "items": [c["construct_id"] for c in construct_rows if c["self_visibility"] is None]},
        {"topic": "可选项文字里夹有说明的决策过程（说明已从可选项中拿出，放在 notes）",
         "items": [m["dm_id"] for m in mechanism_rows if m["notes"]]},
        {"topic": "登记 §4.10 先验映射表的候选项不在 §7 该过程的可选项里",
         "items": _prior_candidates(src, mechanisms)},
    ]
    return {"schema_version": SCHEMA_VERSION, "status": "psychology_group_v1_definition",
            "evidence": "来源理论 + 心理学组工程化约定；其中数值为初始值，待标定，不是验证结果",
            "generated_by": "python3 -m rolesim registry", "sources": src.manifest(),
            "counts": {"constructs": len(construct_rows), "mechanisms": len(mechanism_rows),
                       "operators": len(operator_rows)},
            "constructs": construct_rows, "mechanisms": mechanism_rows, "operators": operator_rows,
            "review": review}


# -- IRegistry -----------------------------------------------------------------
def _frozen(row, **changes):
    return {**row, "basis": MappingProxyType(dict(row["basis"])), **changes}


class Registry:
    """IRegistry over data produced by build_registry (by default data/registry.json)."""

    def __init__(self, data):
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"registry schema {data.get('schema_version')!r}, expected {SCHEMA_VERSION}")
        self._constructs = {r["construct_id"]: Construct(**_frozen(r)) for r in data["constructs"]}
        self._mechanisms = {
            r["dm_id"]: DecisionMechanism(**_frozen(
                r, options=tuple(r["options"]), stages=tuple(r["stages"]), hands_to=tuple(r["hands_to"]),
                notes=tuple(r["notes"]),
                option_groups=tuple((g["label"], tuple(g["items"])) for g in r["option_groups"])))
            for r in data["mechanisms"]}
        self._operators = {r["name"]: OperatorRule(**_frozen(r, writes=tuple(r["writes"]), stages=tuple(r["stages"])))
                           for r in data["operators"]}
        self.sources = tuple(data["sources"])
        self.review = tuple(data["review"])

    @classmethod
    def load(cls, path=REGISTRY_PATH):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def construct(self, construct_id):
        try:
            return self._constructs[construct_id]
        except KeyError:
            raise KeyError(f"unregistered construct: {construct_id}") from None

    def mechanism(self, dm_id):
        try:
            return self._mechanisms[dm_id]
        except KeyError:
            raise KeyError(f"unregistered decision process: {dm_id}") from None

    def operator(self, name):
        try:
            return self._operators[name]
        except KeyError:
            raise KeyError(f"unregistered update operator: {name}") from None

    def ids(self, kind):
        tables = {"construct": self._constructs, "mechanism": self._mechanisms, "operator": self._operators}
        if kind not in tables:
            raise ValueError(f"kind must be one of {sorted(tables)}")
        return tuple(tables[kind])
