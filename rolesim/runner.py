"""ITurnRunner (plan/10 §1.1): one act of one role, through the three stages and the store.

run(key, new_records, present):
  1. New records are grouped by action_id, in order. Each group is one event phase:
     S1.run -> IStateStore.commit.
  2. Feedback: the first group in which someone our last plan addressed (anyone else, if it
     addressed nobody in particular) speaks or acts towards us opens a feedback phase:
     S3.run -> commit, once per plan (状态变化规则 §3.4: 他人对本角色作出反应).
  3. Then a plan phase: S2.run -> commit. If the plan's own dependency group (goal, commitment,
     strategy) is rejected, the action is not sent as planned and S2 runs again in a new phase
     with the rejections in upstream["rejected"] (行动计划 skill §4.3); a second rejection fails.
  4. IExpresser.realize on the committed snapshot -> AgentActionContent.
Every phase reads the snapshot left by the previous commit, and each commit is written to ITrace
as a PhaseLog.

Failure keeps what was committed: groups whose S1 committed are taken in, a pending feedback or
an expression still owed is remembered, so retrying the act resumes instead of committing the
same phase twice. Any stage failure fails the act (StageError).
"""
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .agent_api import ENV_ID, record_id
from .interfaces import FULL_MODE, InterfaceError, PhaseLog, Phase, StageContext, StageError

PLAN_GROUP = "goal_commitment_strategy"


@dataclass
class _Session:
    stable: Any
    background: dict
    history: list = field(default_factory=list)       # records taken in, oldest first
    absorbed: set = field(default_factory=set)        # their ids
    latest_s1: Any = None                             # last committed SituationInterpretation
    last_plan: Any = None                             # last ActionPlan sent
    plan_judged: bool = True                          # its feedback has been (or is being) judged
    pending_feedback: Optional[tuple] = None          # (action_id, group, S1 product) awaiting S3
    pending_plan: Optional[tuple] = None              # (ActionPlan, StageContext) committed, not yet expressed
    phase_no: int = 0
    mode: str = FULL_MODE
    skills: tuple = ()
    briefing: Any = None
    latest_cues: dict = field(default_factory=dict)   # S1's skill cues for the coming plan


class TurnRunner:
    """ITurnRunner over IStage x3, IStateStore, IExpresser and an optional ITrace."""

    def __init__(self, store, stages, expresser, trace=None, replans=1):
        self.store, self.stages, self.expresser, self.trace = store, stages, expresser, trace
        self.replans = replans
        self._sessions = {}

    def begin(self, key, state, stable, background=None, mode=FULL_MODE, skills=(), briefing=None):
        """Open a session from its entry state (IRoleRepository.enter) and stable profile; `mode`,
        `skills` and `briefing` reach every stage of this session."""
        if key in self._sessions:
            raise InterfaceError(f"{key}: already begun")
        self.store.open(key, state)
        self._sessions[key] = _Session(stable, dict(background or {}), mode=mode, skills=tuple(skills),
                                       briefing=briefing)

    def started(self, key):
        return key in self._sessions

    def session(self, key):
        if key not in self._sessions:
            raise InterfaceError(f"{key}: not begun")
        return self._sessions[key]

    # -- one act ---------------------------------------------------------------------------
    def run(self, key, new_records, present):
        s = self.session(key)
        me = key[1]
        seen_before = list(s.history)
        fresh = [r for r in new_records if record_id(r) not in s.absorbed]
        if s.pending_feedback:
            self._feedback(key, s, present)
        groups = {}
        for record in fresh:
            groups.setdefault(record["action_id"], []).append(record)
        for action_id, group in groups.items():
            out, _, _ = self._phase(key, s, "S1", "event", action_id, group, present,
                                    {"S1": s.latest_s1, "plan": s.last_plan})
            s.latest_s1, s.latest_cues = out.product, dict(out.cues)
            s.history.extend(group)
            s.absorbed.update(record_id(r) for r in group)
            if self._responds(s, me, group):
                s.pending_feedback, s.plan_judged = (action_id, group, out.product), True
                self._feedback(key, s, present)
        if s.pending_plan is None:
            s.pending_plan = self._plan(key, s, present, fresh, seen_before)
        plan, ctx = s.pending_plan
        content = self.expresser.realize(plan, replace(ctx, state=self.store.snapshot(key)))
        s.pending_plan, s.last_plan, s.plan_judged = None, plan, False
        return content, self.store.snapshot(key)

    # -- pieces -------------------------------------------------------------------------------
    def _phase(self, key, s, stage, kind, event_id, records, present, upstream, history=None):
        s.phase_no += 1
        state = self.store.snapshot(key)
        ctx = StageContext(Phase(event_id, s.phase_no, kind), state, s.stable, tuple(records),
                           {k: v for k, v in upstream.items() if v is not None}, dict(present), key,
                           tuple(s.history if history is None else history), s.background, s.mode, s.skills,
                           s.briefing)
        output = self.stages[stage].run(ctx)
        result = self.store.commit(ctx, output)
        if self.trace is not None:
            self.trace.phase(key, PhaseLog(event_id, s.phase_no, state.state_id, result.output_state_id, output.evidence,
                                           output.judgments, {stage: output.product}, result.accepted, result,
                                           dict(state.versions)))
        return output, ctx, result

    @staticmethod
    def _responds(s, me, group):
        """Someone our last plan addressed acts towards us: feedback on that plan."""
        if s.last_plan is None or s.plan_judged:
            return False
        audience = set(s.last_plan.expression.audience)
        for r in group:
            actor = r["actor"]["actor_id"]
            if actor not in (me, ENV_ID) and r["kind"] != "thought" and me in r["recipients"] \
                    and (not audience or actor in audience):
                return True
        return False

    def _feedback(self, key, s, present):
        action_id, group, s1 = s.pending_feedback
        self._phase(key, s, "S3", "feedback", action_id, group, present, {"plan": s.last_plan, "S1": s1})
        s.pending_feedback = None

    def _plan(self, key, s, present, fresh, seen_before):
        rejected = None
        for _ in range(self.replans + 1):
            out, ctx, result = self._phase(key, s, "S2", "plan", "plan", fresh, present,
                                           {"S1": s.latest_s1, "plan": s.last_plan, "rejected": rejected,
                                            "cues": s.latest_cues or None},
                                           history=seen_before)
            refused = [r for r in result.rejected if r.error.kind == "hard" and r.group == PLAN_GROUP]
            if not refused:
                return out.product, ctx
            rejected = [{"construct_id": r.change.construct_id, "key": r.change.key, "code": r.error.code,
                         "detail": r.error.detail} for r in refused]
        raise StageError(f"S2: the plan's goal / commitment / strategy changes were rejected "
                         f"{self.replans + 1} times; nothing is sent ({rejected})")
