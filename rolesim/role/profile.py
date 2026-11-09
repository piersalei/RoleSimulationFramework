"""IProfileCompiler (plan/10 §2.1): a profile -> StableProfile + review items.

A profile ("profile-1", profiles/<id>.json) is keyed by the registry's stable construct ids:

  {"format": "profile-1", "id": "role1", "name": "林澄", "summary": "...", "self_view": "...",
   "text": "natural-language profile (optional)",
   "stable": {"s.traits.attachment.global": {"value": {"anxiety": 0.8, "avoidance": -0.3},
                                             "source": "explicit", "basis": "...", "confidence": 0.8,
                                             "phrase": "conditioned natural-language form"}, ...},
   "entry": {"d.relation.trust": {"role2": {"value": 0.6}}, ...},      # explicit entry state
   "provenance": "who wrote it and on what evidence"}

compile(raw, config), following 角色状态说明v1 §8 and 心智建模库 5.1 / 5.4:
  explicit       values written in the profile (source explicit, or inferred / default with a
                 basis); one personality scheme at most, the other two disabled
  model_inferred from `text` through IModel ("profile" call), only for constructs not written
                 explicitly; the quote must occur verbatim in the text, or the candidate goes to
                 review instead of the profile
  default        social-cognition items that are not given (取 0 并标为 default)
  unknown        everything else, with the reason; never a random value
  disabled       turned off by the enable configuration
  review         high-impact fields that are not explicit (§8.3); constructs the role cannot be
                 aware of that have no conditioned phrase (they reach no prompt, 心智建模库 5.4);
                 rejected model candidates; dropped phrases
Phrases may not carry system ids, digits or 总是 / 绝不 (5.4).

Value forms are checked for the modules whose numbers code downstream reads (P1 facets,
attachment axes, cognitive style, Schwartz weights, 71-dimension levels, motivation and
function ranges); the rest must be plain finite JSON. Not done: the §4.10 prior mapping for
decision preferences and derived fields (P2 global factors, HEXACO domains, labels).
"""
import json
import math
import re
from typing import Mapping

from ..catalog import digest
from ..interfaces import (EnableConfig, InterfaceError, PromptView, ReviewItem, StableModule, StableProfile,
                         STATUSES)
from .persona import FACET_KEYS, SCHWARTZ_VALUES

PROFILE_FORMAT = "profile-1"
PROFILE_KEYS = {"format", "id", "name", "summary", "self_view", "text", "stable", "entry", "setup", "skills", "provenance"}
SKILL_KEYS = {"name", "priority", "note"}
ENTRY_KEYS = {"value", "source", "basis", "confidence", "phrase", "status", "reason"}
SOURCES = ("explicit", "inferred", "model_inferred", "default")
SCHEMES = ("s.traits.personality.p1_big_five", "s.traits.personality.p2_16pf", "s.traits.personality.p3_jung_hexaco")
SOCIAL_COGNITION = "s.traits.social_cognition_71d"
# 角色状态说明 §8.4 / 心智建模库 5.1: identity -> personality and attachment -> motivation, affect,
# 71d, cognitive style -> development, self system, personality function -> values, core goals,
# speech style -> decision preferences.
ASSEMBLY = ("s.identity.", "s.traits.personality.", "s.traits.attachment.", "s.traits.motivation.", "s.traits.affect.",
            SOCIAL_COGNITION, "s.traits.cognitive.", "s.traits.development.", "s.traits.self.", "s.traits.function.",
            "s.values.", "s.core_goals.", "s.speech_style.", "s.traits.decision_preferences")
FORBIDDEN_IN_PHRASE = re.compile(r"[sdm]\.[a-z_]+|[0-9]|总是|绝不|永远不")
REVIEW_HIGH_IMPACT = "高影响字段且非档案明写，需审核（角色状态说明 §8.3）"
REVIEW_NO_PHRASE = "角色不能自知（或来源没写自知程度），又没有条件化语句：不会进入任何提示（心智建模库 5.4）"


# -- value forms ---------------------------------------------------------------------------------
def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _leaves(value):
    if isinstance(value, Mapping):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)
    else:
        yield value


def _z(value):
    return _number(value) and -2 <= value <= 2


def _keyed_z(keys):
    def check(value):
        if not isinstance(value, Mapping) or not value:
            return "expected a non-empty map"
        bad = [k for k in value if k not in keys]
        if bad:
            return f"unknown keys {bad}"
        return None if all(_z(v) for v in value.values()) else "values must lie in -2..+2"
    return check


def _scalar_z(value):
    return None if _z(value) else "expected a number in -2..+2"


def _numbers_within(low, high):
    def check(value):
        numbers = [v for v in _leaves(value) if _number(v)]
        return None if all(low <= v <= high for v in numbers) else f"numbers must lie in {low}..{high}"
    return check


def _schwartz(value):
    weights = value.get("weights") if isinstance(value, Mapping) else None
    if not isinstance(weights, Mapping) or not weights:
        return "expected {'weights': {value: weight}}"
    bad = [k for k in weights if k not in SCHWARTZ_VALUES]
    if bad:
        return f"unknown values {bad}"
    if not all(_number(w) and w >= 0 for w in weights.values()):
        return "weights must be non-negative numbers"
    return None if abs(sum(weights.values()) - 1) <= 0.02 else "weights must sum to 1"


def _levels_71d(value):
    if not isinstance(value, Mapping):
        return "expected {item: level}"
    bad = [k for k in value if not re.fullmatch(r"[123]\.\d\.\d", str(k))]
    if bad:
        return f"not 71-dimension item ids: {bad}"
    return None if all(v in (-2, -1, 0, 1, 2) and not isinstance(v, bool) for v in value.values()) else "levels must be -2..+2 integers"


FORMS = {
    "s.traits.personality.p1_big_five": _keyed_z(FACET_KEYS),
    "s.traits.attachment.global": _keyed_z({"anxiety", "avoidance"}),
    "s.traits.cognitive.dual_process": _scalar_z, "s.traits.cognitive.need_for_cognition": _scalar_z,
    "s.traits.cognitive.ambiguity_tolerance": _scalar_z, "s.traits.cognitive.flexibility": _scalar_z,
    "s.traits.cognitive.need_for_closure": _keyed_z({"urgency", "fixation"}),
    "s.values.schwartz_priorities": _schwartz,
    "s.values.cultural_orientation": _numbers_within(-2, 2),
    SOCIAL_COGNITION: _levels_71d,
    "s.traits.function.domains": _numbers_within(0, 4),
    **{f"s.traits.motivation.{m}": _numbers_within(0, 1)
       for m in ("source_activation", "need_vector", "goal_computation", "conflict_regulation", "self_efficacy")},
}


def check_form(construct_id, value):
    """None when `value` fits the construct's checked form, else the problem."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return "not plain finite JSON"
    form = FORMS.get(construct_id)
    return form(value) if form else None


def check_phrase(phrase):
    if phrase is None:
        return None
    if not isinstance(phrase, str) or not 6 <= len(phrase.strip()) <= 400:
        return "a phrase is 6-400 characters of text"
    found = FORBIDDEN_IN_PHRASE.search(phrase)
    return f"contains {found.group(0)!r} (system id, digit, or 总是 / 绝不)" if found else None


# -- the compiler ------------------------------------------------------------------------------
def _order(registry):
    stable = [cid for cid in registry.ids("construct") if registry.construct(cid).layer == "stable"]
    rank = {cid: next((i for i, p in enumerate(ASSEMBLY) if cid.startswith(p)), len(ASSEMBLY)) for cid in stable}
    return sorted(stable, key=lambda cid: (rank[cid], stable.index(cid)))


class ProfileCompiler:
    """IProfileCompiler over the registry; `model` (IModel) reads natural-language text."""

    def __init__(self, registry, model=None):
        self.registry = registry
        self.model = model

    def _normalize(self, raw):
        if isinstance(raw, str):
            raw = {"format": PROFILE_FORMAT, "text": raw}
        if not isinstance(raw, Mapping) or raw.get("format") != PROFILE_FORMAT:
            raise InterfaceError(f"a profile is text or a {PROFILE_FORMAT!r} object")
        extra = set(raw) - PROFILE_KEYS
        if extra:
            raise InterfaceError(f"profile: unexpected fields {sorted(extra)}")
        if raw.get("setup") is not None:
            from .briefing import validate_briefing
            from ..run.scene import RESERVED_IDS
            try:
                validate_briefing(raw["setup"], RESERVED_IDS)
            except ValueError as exc:
                raise InterfaceError(f"profile.setup: {exc}") from None
        skills = raw.get("skills") or []
        if not isinstance(skills, list) or any(not isinstance(i, Mapping) or set(i) != SKILL_KEYS for i in skills):
            raise InterfaceError(f"profile.skills: a list of {sorted(SKILL_KEYS)}")
        stable = raw.get("stable") or {}
        if not isinstance(stable, Mapping):
            raise InterfaceError("profile.stable must map construct ids to entries")
        for cid, entry in stable.items():
            try:
                construct = self.registry.construct(cid)
            except KeyError:
                raise InterfaceError(f"profile.stable: unregistered construct {cid}") from None
            if construct.layer != "stable":
                raise InterfaceError(f"profile.stable: {cid} is not a stable construct (dynamic values go in entry)")
            if not isinstance(entry, Mapping) or set(entry) - ENTRY_KEYS:
                raise InterfaceError(f"profile.stable.{cid}: an entry has only {sorted(ENTRY_KEYS)}")
            status = entry.get("status", "active")
            if status not in STATUSES:
                raise InterfaceError(f"profile.stable.{cid}: status {status!r}")
            if status == "active":
                if "value" not in entry or entry["value"] is None:
                    raise InterfaceError(f"profile.stable.{cid}: an active entry needs a value")
                if entry.get("source", "explicit") not in SOURCES:
                    raise InterfaceError(f"profile.stable.{cid}: source must be one of {SOURCES}")
                if entry.get("source", "explicit") in ("inferred", "model_inferred") and not entry.get("basis"):
                    raise InterfaceError(f"profile.stable.{cid}: an inferred value needs its basis")
                problem = check_form(cid, entry["value"])
                if problem:
                    raise InterfaceError(f"profile.stable.{cid}: {problem}")
                confidence = entry.get("confidence")
                if confidence is not None and not (_number(confidence) and 0 <= confidence <= 1):
                    raise InterfaceError(f"profile.stable.{cid}: confidence must lie in 0..1")
        return raw

    def _from_text(self, profile, given, review):
        from ..stage.schemas import profile_schema, profile_task
        text = profile["text"]
        view = PromptView({}, (), "", {}, profile.get("id", ""), {}, profile_task(self.registry, set(given)),
                          {"档案原文": text})
        result = self.model.complete("profile", view, profile_schema(self.registry))
        found = {}
        for item in result.output["modules"]:
            cid, value, quote = item["construct_id"], item["value"], item["quote"]
            if cid in given or cid in found:
                reason = "档案已明写，模型推断不采用" if cid in given else "同一构念推断了两次，只取第一次"
            elif quote not in text:
                reason = "引文不在档案原文里"
            else:
                problem = check_form(cid, value)
                if problem is None:
                    found[cid] = {"value": value, "source": "model_inferred", "basis": quote, "phrase": item["phrase"]}
                    continue
                reason = f"取值不合形式：{problem}"
            review.append(ReviewItem(cid, value, "model_inferred", quote, f"模型推断未采用：{reason}"))
        return found

    def compile(self, raw, config=None):
        config = config or EnableConfig()
        profile = self._normalize(raw)
        given = dict(profile.get("stable") or {})
        review = []
        inferred = self._from_text(profile, given, review) if profile.get("text") and self.model is not None else {}
        schemes = [c for c in SCHEMES if c in given and given[c].get("status", "active") == "active"]
        if len(schemes) > 1:
            raise InterfaceError(f"profile: one personality scheme at most, got {schemes}")
        modules = {}
        for cid in _order(self.registry):
            construct = self.registry.construct(cid)
            entry = given.get(cid) or inferred.get(cid)
            if not config.enabled(cid):
                modules[cid] = StableModule(cid, None, "disabled", "unknown", None, reason="启用配置关闭")
                continue
            if cid in SCHEMES and schemes and cid not in schemes:
                modules[cid] = StableModule(cid, None, "disabled", "unknown", None, reason="未启用的人格方案（三套互斥）")
                continue
            if entry is None:
                modules[cid] = (StableModule(cid, {}, "active", "default", None, reason="未赋值条目取 0 并标为 default（角色状态说明 §8.1）")
                                if cid == SOCIAL_COGNITION else
                                StableModule(cid, None, "unknown", "unknown", None, reason="档案未提供"))
                continue
            status = entry.get("status", "active")
            if status != "active":
                modules[cid] = StableModule(cid, None, status, "unknown", None, reason=entry.get("reason") or "档案标注")
                continue
            source = entry.get("source", "explicit")
            phrase = entry.get("phrase")
            problem = check_phrase(phrase)
            if problem:
                review.append(ReviewItem(cid, phrase, source, None, f"条件化语句已去掉：{problem}"))
                phrase = None
            needs_review = construct.high_impact and source != "explicit"
            basis = entry.get("basis")
            modules[cid] = StableModule(cid, entry["value"], "active", source, entry.get("confidence"), needs_review,
                                        basis if source in ("explicit", "model_inferred") else None, phrase,
                                        basis if source in ("inferred", "default") else None)
            if needs_review:
                review.append(ReviewItem(cid, entry["value"], source, basis, REVIEW_HIGH_IMPACT))
            if construct.self_visibility not in ("aware", "partial") and phrase is None:
                review.append(ReviewItem(cid, entry["value"], source, basis, REVIEW_NO_PHRASE))
        snapshot = digest({"id": profile.get("id"), "modules": {cid: [m.value, m.status, m.source_kind, m.phrase]
                                                                for cid, m in modules.items()}})
        return StableProfile(snapshot, profile.get("id") or "", modules), review


# -- the hand-written role files (persona schema 2.0) --------------------------------------------
# (persona path under "stable", persona module_status key, construct id, how to read the value)
LEGACY = (
    ("traits.personality", "stable.traits.personality", "s.traits.personality.p1_big_five",
     lambda v: v.get("facets") if isinstance(v, dict) and v.get("scheme") == "P1" else None),
    ("traits.attachment", "stable.traits.attachment", "s.traits.attachment.global", lambda v: v),
    ("values.schwartz_priorities", "stable.values.schwartz_priorities", "s.values.schwartz_priorities", lambda v: v),
    ("core_goals", "stable.core_goals", "s.core_goals.primary", lambda v: v),
    ("speech_style", "stable.speech_style.baseline", "s.speech_style.baseline", lambda v: v),
    ("identity.social_roles", "stable.identity.social_roles", "s.identity.social_roles", lambda v: v),
)


def _at(tree, path):
    for part in path.split("."):
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


def profile_from_persona(persona, role_id):
    """A profile-1 object from a role file's persona: the modules that map one-to-one onto
    registered constructs and are active there, as explicit values."""
    stable = persona["stable"]
    status = stable.get("module_status", {})
    entries = {}
    for path, status_key, cid, read in LEGACY:
        value = _at(stable, path)
        if value is None or status.get(status_key, {}).get("status") != "active":
            continue
        value = read(value)
        if value is not None:
            entries[cid] = {"value": value, "source": "explicit",
                            "basis": f"角色文件 persona（schema {persona.get('schema_version')}）stable.{path}"}
    profile = {"format": PROFILE_FORMAT, "id": role_id, "name": persona["name"], "stable": entries, "entry": {},
               "provenance": "由手写角色文件转换；示例人物的人工设定值，不是量表测量"}
    for key, source in (("summary", "public_description"), ("self_view", "self_view")):
        if persona.get(source):
            profile[key] = persona[source]
    return profile


def profile_from_role(role):
    """A profile-1 object from a loaded role file (roles.load_role): the persona's mappable modules,
    the opening setup and the skill binding."""
    profile = profile_from_persona(role["persona"], role["id"])
    if role.get("setup") is not None:
        profile["setup"] = role["setup"]
    items = (role.get("file") or {}).get("skills") or []
    if items:
        profile["skills"] = [dict(i) for i in items]
    return profile
