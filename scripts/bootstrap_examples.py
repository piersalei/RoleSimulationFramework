"""Rebuild fictional, hand-authored fixtures. Not population/personality sampling.

Two packages, kept apart: a persona package (examples/personas/: persona schema 2.0 plus a
skills binding) and a scene package (examples/scenes/: events, world data and per-role
briefings).
"""
import copy
import json
from pathlib import Path

from rolesim.modules import STABLE_MODULES, entry
from rolesim.persona import FACET_KEYS, SCHEMA_VERSION
from rolesim.skills import BINDING_VERSION

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'synthetic_authoring_fixture; 非量表测量、非心理学同学提供的实证数值'


def save(name, data):
    (ROOT / 'examples' / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def binding(persona_id, items):
    note = '工程侧按画像拟定的绑定与优先级，未经心理学组审定：'
    return {'schema_version': BINDING_VERSION, 'persona_id': persona_id,
            'skills': [{'name': n, 'priority': p, 'note': note + why} for n, p, why in items]}


def normalise(raw):
    """Weights over the listed values only; unlisted Schwartz values stay unknown, not zero."""
    total = sum(raw.values())
    weights = {k: round(v / total, 6) for k, v in raw.items()}
    last = list(weights)[-1]
    weights[last] = round(1 - sum(v for k, v in weights.items() if k != last), 6)
    return weights


SOURCES = {
    'stable.identity.social_roles': 'mind.1.3',
    'stable.traits.personality': 'mind.2.1.2', 'stable.traits.attachment': 'mind.2.1.5',
    'stable.traits.motivation_baseline': 'mind.2.2.3', 'stable.traits.affective_capacities': 'mind.2.3.2',
    'stable.traits.cognitive_style': 'mind.2.5', 'stable.values.schwartz_priorities': 'mind.4.1',
    'stable.core_goals': 'mind.2.2.4', 'stable.speech_style.baseline': 'mind.1.4',
    'stable.speech_style.situational_rules': 'mind.1.4',
    'stable.mechanism_priors': 'decision.11.1'}
INACTIVE = {
    'stable.identity.demographics': ('unknown', '档案未提供'),
    'stable.identity.resources': ('unknown', '档案未提供'),
    'stable.identity.life_history': ('unknown', '档案未提供'),
    'stable.traits.social_cognition_71d': ('not_modeled', '源文档引用的71维具体定义未提供，禁止以自编维度冒充'),
    'stable.traits.developmental_position': ('not_modeled', '需要心理学同学确认具体行为锚点'),
    'stable.traits.personality_function': ('not_modeled', '荣格功能属于 P3，本版只执行 P1'),
    'stable.values.investment': ('unknown', '总体价值投入度未设定'),
    'stable.values.moral_reasoning': ('not_modeled', '道德推理子模块待审定'),
    'stable.values.cultural_orientation': ('not_modeled', '文化取向子模块待审定')}


def module_status():
    table = {}
    for path in STABLE_MODULES:
        if path in INACTIVE:
            table[path] = entry(*INACTIVE[path], version=SCHEMA_VERSION)
        else:
            table[path] = entry('active', '示例人物的人工设定值', source=[SOURCES[path]], origin='profile',
                                version=SCHEMA_VERSION)
    assert set(table) == set(STABLE_MODULES)
    return table


def provenance():
    out = {path: {'source_ref': ref, 'assignment': FIXTURE, 'confidence': 0.0} for path, ref in SOURCES.items()}
    out['stable.traits.affective_capacities']['assignment'] = (
        'engineering projection: threat=(anxiety+2)/4; recovery=.8*(2-vulnerability)/4; uncalibrated')
    return out


base = {
    'schema_version': SCHEMA_VERSION, 'id': 'cautious', 'name': '林澄',
    'public_description': '虚构研究项目成员，负责核验与交付数据，表达较委婉。',
    'self_view': ['我希望把承诺的工作做好，也不想让合作方失望。', '资料不清楚时，我会想先确认一下。'],
    'stable': {
        'identity': {'demographics': None, 'social_roles': ['研究项目成员，负责核验与交付数据'],
                     'resources': None, 'life_history': None},
        'traits': {
            'personality': {'scheme': 'P1', 'facets': {k: 0 for k in sorted(FACET_KEYS)}},
            'attachment': {'anxiety': .8, 'avoidance': -.3},
            'motivation_baseline': {'competence': .8, 'relatedness': .8, 'autonomy': .4},
            'affective_capacities': {'threat_sensitivity': .9, 'recovery_rate': .16},
            'social_cognition_71d': None,
            'cognitive_style': {'verification_bias': .9},
            'developmental_position': None, 'personality_function': None},
        'values': {
            'schwartz_priorities': {'weights': normalise({'benevolence_dependability': .85, 'security_personal': .7,
                                                          'self_direction_action': .4}),
                                    'conflict_thresholds': {}, 'concession_ranges': {}},
            'investment': None, 'moral_reasoning': None, 'cultural_orientation': None},
        'core_goals': [{'id': 'g1', 'text': '按承诺交付可核验的工作', 'priority': .9, 'stability': .8,
                        'source': FIXTURE, 'supports': [{'id': 'g1a', 'text': '承诺前先确认要求与可用记录'}]}],
        'speech_style': {'baseline': {'directness': .3},
                         'situational_rules': [{'when': '被催促或需要拒绝时', 'style': '先承认对方处境，再说明需要核对的条件'}]},
        'mechanism_priors': {'verify': .95, 'appraise': .7, 'trust': .7, 'repair': .85, 'boundary': .25},
        'module_status': module_status(),
    },
    'provenance': provenance(),
}
base['stable']['traits']['personality']['facets'].update({
    'neuroticism.anxiety': 1.6, 'neuroticism.vulnerability': 1.2,
    'conscientiousness.deliberation': 1.4, 'conscientiousness.dutifulness': 1.2,
    'extraversion.assertiveness': -.8, 'agreeableness.compliance': 1.3,
})
save('personas/cautious.json', base)
save('personas/cautious.skills.json', binding('cautious', [
    ('verify-before-commit', .95, '核查偏好高、审慎分面高'),
    ('selective-disclosure', .9, '焦虑分面高，倾向回避暴露自身不足'),
    ('staged-commitment', .8, '尽责且审慎，偏好可兑现的小承诺'),
    ('acknowledge-first', .8, '表达直接性低，先照顾对方处境'),
    ('accept-verified-repair', .75, '修复机制先验高，但依赖兑现证据'),
    ('soft-boundary', .7, '顺从分面高，边界以委婉方式表达'),
    ('pause-under-pressure', .6, '威胁敏感度高、恢复慢'),
    ('proactive-disclosure', .4, '主张性低，较少主动陈述'),
]))
other = copy.deepcopy(base)
other.update(id='assertive', name='周越', public_description='虚构研究项目成员，负责核验与交付数据，表达较直接。', self_view=['我愿意推进工作，也会说明我能承担到什么程度。', '合作应有清楚的责任和可核验的条件。'])
t = other['stable']['traits']
t['personality']['facets'].update({
    'neuroticism.anxiety': -1.2, 'neuroticism.vulnerability': -1.0,
    'conscientiousness.deliberation': .7, 'conscientiousness.dutifulness': 1.2,
    'extraversion.assertiveness': 1.6, 'agreeableness.compliance': -1.0,
})
t.update(attachment={'anxiety': -.6, 'avoidance': .3},
         affective_capacities={'threat_sensitivity': .2, 'recovery_rate': .6},
         cognitive_style={'verification_bias': .35},
         motivation_baseline={'competence': .8, 'relatedness': .4, 'autonomy': .8})
other['stable']['speech_style'] = {'baseline': {'directness': .9},
                                   'situational_rules': [{'when': '分歧出现时', 'style': '直接说明自己能承担的范围和条件'}]}
other['stable']['mechanism_priors'] = {'verify': .25, 'appraise': .3, 'trust': .65, 'repair': .45, 'boundary': .95}
other['stable']['values']['schwartz_priorities']['weights'] = normalise(
    {'benevolence_dependability': .7, 'security_personal': .4, 'self_direction_action': .85})
other['stable']['core_goals'] = [{'id': 'g1', 'text': '推进交付，同时明确自己能承担的范围', 'priority': .9, 'stability': .8,
                                  'source': FIXTURE, 'supports': [{'id': 'g1a', 'text': '把责任与可核验条件说清楚'}]}]
save('personas/assertive.json', other)
save('personas/assertive.skills.json', binding('assertive', [
    ('firm-boundary', .95, '主张性高、表达直接性高'),
    ('proactive-disclosure', .85, '倾向主动摆出条件和理由'),
    ('staged-commitment', .85, '愿意推进，同时说清可承担范围'),
    ('accept-verified-repair', .6, '修复机制先验中等'),
    ('selective-disclosure', .6, '焦虑分面低，隐瞒动机较弱'),
    ('verify-before-commit', .4, '核查偏好低'),
]))

def event(i, content, cues, signal=0, readers=None, kind='observation'):
    return {'id': i, 'text': content, 'readers': readers or ['*'], 'cues': cues, 'reliability_signal': signal, 'kind': kind}

scenario = {
    'schema_version': '1.1', 'id': 'cooperation_conflict_v1',
    'goal': '在不做无法兑现的承诺的前提下，与合作方明确可核验的交付范围。',
    'partner': '合作方负责人',
    'allowed_actions': ['request_evidence', 'ask_clarification', 'propose_plan', 'set_boundary', 'wait'],
    'events': [
        event('e1', '合作方催促：今天能否交出全部数据？你尚未收到完整的状态记录。', {'uncertainty': .9, 'pressure': .6, 'conflict': .9}),
        event('e2', '你核对了约定的收件箱：合作方此前承诺补发的说明仍未到达。', {'commitment': .9, 'uncertainty': .6, 'pressure': .8, 'conflict': .8}, -.7),
        event('e3', '合作方提出先核验一部分记录，再一起协商剩余数据的时间；这仍是一项提议。', {'repair': .9, 'commitment': .5, 'uncertainty': .3}, kind='claim'),
        event('e4', '核验完成的记录已按新约定收到，你检查了收件记录。', {'repair': .8, 'commitment': .9}, .8),
        event('partner_private', '只有合作方收到内部通知：下周的人员安排可能改变。', {}, readers=['partner']),
    ],
    'evidence': event('status_receipt', '你获得一份状态回执：部分数据仍在复核；回执没有给出最终完成时间。', {'uncertainty': .2, 'pressure': .1}),
    'world_private': {'do_not_disclose': 'HIDDEN_WORLD_SENTINEL_47', 'purpose': '知识隔离测试；不传给角色或表达模型。'},
    # '*' applies to whichever persona fills the focal slot; a role-specific key would override it.
    'briefings': {'*': {
        'goal': '只承诺能核实的部分，同时不让合作关系破裂。',
        'disclose': [{'id': 'd1', 'text': '前两批记录你已经核对完毕，今天可以先交付这部分。', 'markers': ['前两批']}],
        'conceal': [{'id': 'c1', 'text': '你这边第三批数据其实还没清洗完，即使对方的说明今天到了，也来不及全部交付。',
                     'markers': ['没清洗完', '未清洗', '还没清洗', '第三批'], 'strategy': 'omission'}],
        'recap': [{'id': 'recap:r1', 'text': '上周例会上，合作方承诺本周一前补发数据说明；你答应收到说明后三天内交付全部数据。'}],
    }},
}
save('scenes/cooperation_conflict.json', scenario)

# Five-turn variant: after the partial delivery the partner presses on the remainder,
# which puts the concealed item under direct questioning.
followup = copy.deepcopy(scenario)
followup['id'] = 'cooperation_conflict_followup_v1'
followup['events'].insert(4, event('e5', '合作方追问：剩下的数据具体卡在哪一步？能不能给一个全部交付的确切日期？',
                                   {'pressure': .8, 'uncertainty': .5, 'conflict': .6, 'commitment': .7}, kind='claim'))
save('scenes/cooperation_conflict_followup.json', followup)
