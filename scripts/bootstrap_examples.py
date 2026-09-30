"""Rebuild fictional, hand-authored fixtures. Not population/personality sampling."""
import copy
import json
from pathlib import Path

from rolesim.persona import FACET_KEYS

ROOT = Path(__file__).resolve().parents[1]

def save(name, data):
    (ROOT / 'examples' / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

sources = {'personality': 'mind.2.1.2', 'attachment': 'mind.2.1.5', 'needs': 'mind.2.2.3',
           'values': 'mind.4.1', 'abilities': 'mind.2.4', 'emotion': 'mind.2.3.2',
           'cognition': 'mind.2.5', 'communication': 'mind.1.4', 'mechanism_priors': 'decision.11.1'}
base = {
    'schema_version': '1.0', 'id': 'cautious', 'name': '林澄',
    'public_description': '虚构研究项目成员，负责核验与交付数据，表达较委婉。',
    'self_view': ['我希望把承诺的工作做好，也不想让合作方失望。', '资料不清楚时，我会想先确认一下。'],
    'stable': {
        'personality': {'scheme': 'P1', 'facets': {k: 0 for k in sorted(FACET_KEYS)}},
        'attachment': {'anxiety': .8, 'avoidance': -.3},
        'needs': {'competence': .8, 'relatedness': .8, 'autonomy': .4},
        'values': {'benevolence_dependability': .85, 'security_personal': .7, 'self_direction_action': .4},
        'abilities': {'status': 'pending_source_definition', 'scores': {}},
        'emotion': {'threat_sensitivity': .9, 'recovery_rate': .16},
        'cognition': {'verification_bias': .9},
        'communication': {'directness': .3},
        'mechanism_priors': {'verify': .95, 'appraise': .7, 'trust': .7, 'repair': .85, 'boundary': .25},
    },
    'provenance': {k: {'source_ref': v, 'assignment': 'synthetic_authoring_fixture; 非量表测量、非心理学同学提供的实证数值', 'confidence': 0.0} for k, v in sources.items()},
    'deferred_modules': {
        'P2_P3': '互斥主方案，v0.1只执行P1。',
        'attachment_runtime': '保存二维基线；首版尚未实现关系特异的依恋激活。',
        'ability_71': '源文档引用的71维具体定义未提供，禁止以自编维度冒充。',
        'full_motivation': '仅三个需要权重作为控制器提示；完整分层目标计算待审定。',
        'full_values': '仅三个示例价值主字段；十九价值完整映射与道德推理待审定。',
        'development_and_functioning': '未纳入示例；需要心理学同学确认具体行为锚点。',
        'group_and_culture': '首版为单焦点角色与脚本环境，未执行群体/文化机制。',
        'long_term_personality_change': '普通轨迹固定人格；长期发展属于后续独立协议。',
    },
}
base['stable']['personality']['facets'].update({
    'neuroticism.anxiety': 1.6, 'neuroticism.vulnerability': 1.2,
    'conscientiousness.deliberation': 1.4, 'conscientiousness.dutifulness': 1.2,
    'extraversion.assertiveness': -.8, 'agreeableness.compliance': 1.3,
})
base['provenance']['emotion']['assignment'] = 'engineering projection: threat=(anxiety+2)/4; recovery=.8*(2-vulnerability)/4; uncalibrated'
save('cautious.json', base)
other = copy.deepcopy(base)
other.update(id='assertive', name='周越', public_description='虚构研究项目成员，负责核验与交付数据，表达较直接。', self_view=['我愿意推进工作，也会说明我能承担到什么程度。', '合作应有清楚的责任和可核验的条件。'])
other['stable']['personality']['facets'].update({
    'neuroticism.anxiety': -1.2, 'neuroticism.vulnerability': -1.0,
    'conscientiousness.deliberation': .7, 'conscientiousness.dutifulness': 1.2,
    'extraversion.assertiveness': 1.6, 'agreeableness.compliance': -1.0,
})
other['stable'].update(attachment={'anxiety': -.6, 'avoidance': .3},
                       emotion={'threat_sensitivity': .2, 'recovery_rate': .6},
                       cognition={'verification_bias': .35}, communication={'directness': .9},
                       mechanism_priors={'verify': .25, 'appraise': .3, 'trust': .65, 'repair': .45, 'boundary': .95})
other['stable']['needs'] = {'competence': .8, 'relatedness': .4, 'autonomy': .8}
other['stable']['values'] = {'benevolence_dependability': .7, 'security_personal': .4, 'self_direction_action': .85}
save('assertive.json', other)

def event(i, content, cues, signal=0, readers=None, kind='observation'):
    return {'id': i, 'text': content, 'readers': readers or ['*'], 'cues': cues, 'reliability_signal': signal, 'kind': kind}

scenario = {
    'schema_version': '1.0', 'id': 'cooperation_conflict_v1',
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
}
save('cooperation_conflict.json', scenario)
