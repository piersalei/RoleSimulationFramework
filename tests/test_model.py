"""IModel (rolesim/model.py) and the stage output schemas (rolesim/schemas.py)."""
import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from rolesim import interfaces as I
from rolesim import slots
from rolesim.model import STAGE_PROMPTS, ChatModel, MockModel, view_payload
from rolesim.providers import ChatBackend
from rolesim.registry import Registry
from rolesim.schemas import (PRODUCTS, parse_output, stage_constructs, stage_mechanisms, stage_schema, stage_task,
                             validate)
from rolesim.store import ConstructStore

REG = Registry.load()
KINDS = {"S1": "event", "S2": "plan", "S3": "feedback"}
PRODUCT_TYPES = {"S1": I.SituationInterpretation, "S2": I.ActionPlan, "S3": I.EvolutionResult}
RECORD = {"action_id": "a7", "turn_index": 3, "order_index": 0, "actor": {"actor_id": "c2", "name": "周越"},
          "kind": "speech", "content": "我听老王说你周五交不了，为什么不早说？", "recipients": {"c1": {"name": "林澄"}}}
PRESENT = {"c1": {"name": "林澄"}, "c2": {"name": "周越"}}
EV = I.EvidenceSet((I.EvidenceRef("a7#0", "witnessed"),))


def view(stage, records=(RECORD,), upstream=None):
    return I.PromptView({"name": "林澄"}, tuple(records), "有点紧张", upstream or {}, "c1", PRESENT, stage_task(stage, REG))


def mock_output(stage, **kw):
    return copy.deepcopy(MockModel(REG).complete(stage, view(stage, **kw), stage_schema(stage, REG)).output)


class Validator(unittest.TestCase):
    def test_subset(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["n", "tags"],
                  "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 4},
                                 "tags": {"type": "array", "maxItems": 2, "items": {"enum": ["a", "b"]}},
                                 "note": {"type": ["string", "null"], "maxLength": 3}}}
        self.assertEqual(validate(schema, {"n": 2, "tags": ["a"], "note": None}), [])
        self.assertEqual(validate(schema, {"n": True, "tags": []}), ["$.n: expected integer, got bool"])
        self.assertEqual(validate(schema, {"n": 5, "tags": ["a", "b", "c"]}),
                         ["$.n: above 4", "$.tags: more than 2 items", "$.tags[2]: 'c' is not one of the allowed values"])
        self.assertEqual(validate(schema, {"tags": [], "x": 1, "note": "long"}),
                         ["$: missing n", "$: unexpected field x", "$.note: longer than 3"])
        self.assertEqual(validate({"type": "number"}, float("nan")), ["$: expected number, got float"])


class StageSchemas(unittest.TestCase):
    def test_processes_and_constructs_follow_the_registry(self):
        for stage in KINDS:
            schema = stage_schema(stage, REG)
            judgments = schema["properties"]["judgments"]["items"]["properties"]
            self.assertEqual(judgments["dm_id"]["enum"], [d for d in REG.ids("mechanism") if stage in REG.mechanism(d).stages])
            constructs = schema["properties"]["proposals"]["items"]["properties"]["construct_id"]["enum"]
            self.assertEqual(constructs, stage_constructs(stage, REG))
            self.assertEqual(("evidence_notes" in schema["properties"]), stage == "S1")
        s1, s2, s3 = (stage_constructs(s, REG) for s in ("S1", "S2", "S3"))
        self.assertIn("d.cognition.belief", s1)
        self.assertNotIn("d.relation.trust", s1)
        self.assertNotIn("d.cognition.belief", s2)
        self.assertIn("d.relation.trust", s3)
        self.assertIn("d.affect.emotion", s3)                                   # only through decay in S3
        emotion = next(c for c in stage_task("S3", REG)["constructs"] if c["construct_id"] == "d.affect.emotion")
        self.assertEqual(emotion["operators"], ["decay_or_recovery"])
        self.assertIn("decay_or_recovery", stage_schema("S3", REG)["properties"]["proposals"]["items"]["properties"]["operator"]["enum"])

    def test_task_carries_options_and_fields(self):
        task = stage_task("S1", REG)
        appraisal = next(m for m in task["mechanisms"] if m["dm_id"] == "dm.affect.primary_secondary_appraisal")
        self.assertEqual(appraisal["options"], list(REG.mechanism("dm.affect.primary_secondary_appraisal").options))
        self.assertEqual(appraisal["hands_to"], ["emotion_update", "self_state_update"])      # goal_update is not S1
        stress = next(c for c in task["constructs"] if c["construct_id"] == "d.affect.stress")
        self.assertEqual((stress["fields"], stress["banded"], stress["operators"]), ({"level": "u"}, ["level"], ["emotion_update"]))
        self.assertEqual(sorted(task["grades"]), ["1", "2", "3", "4"])
        self.assertEqual(len(task["mechanisms"]), len(stage_mechanisms("S1", REG)))
        with self.assertRaises(ValueError):
            stage_task("S4", REG)

    def test_products_are_closed_objects(self):
        for schema in PRODUCTS.values():
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))


class Parse(unittest.TestCase):
    def test_mock_outputs_parse_into_products(self):
        upstream = {}
        for stage in KINDS:
            out = parse_output(stage, mock_output(stage, upstream=upstream), REG, EV)
            self.assertIsInstance(out.product, PRODUCT_TYPES[stage])
            upstream[stage] = out.product
            for j in out.judgments:
                self.assertIn(j.option, REG.mechanism(j.dm_id).options)
                self.assertTrue(set(j.hands_to) <= {op for op in REG.ids("operator") if stage in REG.operator(op).stages})
                self.assertEqual(j.evidence, (I.EvidenceRef("a7#0", "witnessed"),))
            self.assertTrue(out.proposals)
        self.assertEqual(upstream["S2"].depends_on, (upstream["S1"].hypotheses[0].statement,))

    def test_schema_breaks_fail_the_stage(self):
        out = mock_output("S1")
        out["product"]["hypotheses"] *= 4
        with self.assertRaisesRegex(I.StageError, "more than 3 items"):
            parse_output("S1", out, REG, EV)
        out = mock_output("S1")
        out["mood"] = "x"
        with self.assertRaisesRegex(I.StageError, "unexpected field mood"):
            parse_output("S1", out, REG, EV)
        out = mock_output("S1")
        out["judgments"][0]["dm_id"] = "dm.coop.trust_building"               # an S3 process
        with self.assertRaisesRegex(I.StageError, "not one of the allowed values"):
            parse_output("S1", out, REG, EV)
        out = mock_output("S1")
        out["proposals"][0]["construct_id"] = "d.relation.trust"               # not writable in S1
        with self.assertRaises(I.StageError):
            parse_output("S1", out, REG, EV)

    def test_registry_and_product_rules(self):
        out = mock_output("S1")
        out["judgments"][0]["option"] = "愤怒"
        with self.assertRaisesRegex(I.StageError, "has no option"):
            parse_output("S1", out, REG, EV)
        out = mock_output("S1")
        out["proposals"][0]["mechanisms"] = ["dm.cog.attention_filtering"]
        with self.assertRaisesRegex(I.StageError, "not judged here"):
            parse_output("S1", out, REG, EV)
        out = mock_output("S2")
        out["product"]["chosen"] = "回避话题"                                    # a dropped candidate
        with self.assertRaisesRegex(I.StageError, "kept candidate"):
            parse_output("S2", out, REG, EV)

    def test_evidence_notes(self):
        out = mock_output("S1")
        out["evidence_notes"] = [{"event": "a7#0", "channel": "hearsay", "claim": "林澄周五交不了"}]
        parsed = parse_output("S1", out, REG, EV)
        self.assertEqual(parsed.evidence.included, (I.EvidenceRef("a7#0", "hearsay", "林澄周五交不了"),))
        self.assertEqual(parsed.proposals[0].evidence[0].channel, "hearsay")
        for note, message in (({"event": "a7#0", "channel": "hearsay", "claim": None}, "claim"),
                              ({"event": "zz#1", "channel": "inferred", "claim": None}, "has not seen")):
            out["evidence_notes"] = [note]
            with self.assertRaisesRegex(I.StageError, message):
                parse_output("S1", out, REG, EV)
        heard = I.EvidenceSet((I.EvidenceRef("a7#0", "hearsay", "x"),))
        out["evidence_notes"] = [{"event": "a7#0", "channel": "witnessed", "claim": None}]
        with self.assertRaisesRegex(I.StageError, "cannot turn hearsay"):
            parse_output("S1", out, REG, heard)

    def test_unknown_ids_stay_weak_and_visible_to_the_store(self):
        out = mock_output("S1")
        out["proposals"][0]["evidence"] = ["zz#9"]
        parsed = parse_output("S1", out, REG, EV)
        self.assertEqual(parsed.proposals[0].evidence, (I.EvidenceRef("zz#9", "inferred"),))
        store, key = ConstructStore(REG), ("i1", "c1")
        store.open(key, slots.blank_state(REG, "c1", "snap1"))
        ctx = I.StageContext(I.Phase("a7", 1, "event"), store.snapshot(key), I.StableProfile("snap1", "c1", {}), (), {}, {}, key)
        self.assertEqual([r.error.code for r in store.commit(ctx, parsed).rejected], ["evidence_not_visible"])


class MockThroughTheStore(unittest.TestCase):
    def test_all_three_stages_commit_cleanly(self):
        store, key = ConstructStore(REG), ("i1", "c1")
        store.open(key, slots.blank_state(REG, "c1", "snap1"))
        upstream = {}
        for n, stage in enumerate(KINDS, 1):
            out = parse_output(stage, mock_output(stage, upstream=upstream), REG, EV)
            upstream[stage] = out.product
            ctx = I.StageContext(I.Phase("a7", n, KINDS[stage]), store.snapshot(key), I.StableProfile("snap1", "c1", {}),
                                 (RECORD,), upstream, {}, key)
            result = store.commit(ctx, out)
            self.assertEqual(result.rejected, (), stage)
            self.assertEqual(result.soft_flags, (), stage)
            self.assertTrue(result.accepted, stage)
        snap = store.snapshot(key)
        self.assertIn("b-a7#0", snap.dynamic["d.cognition.belief"])
        self.assertEqual(snap.dynamic["d.policy.current_strategy"]["dm.conflict.conflict_mode"]["c2"], "合作")
        self.assertIn("m-a7#0", snap.memory["m.entry"])

    def test_nothing_seen_means_no_proposals(self):
        for stage in KINDS:
            out = parse_output(stage, mock_output(stage, records=()), REG, I.EvidenceSet(()))
            self.assertEqual((out.proposals, out.judgments), ((), ()), stage)


class FakeTransport:
    model = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.sent, self.last_reasoning = reply, error, [], "想了一下"

    def chat(self, system, payload):
        self.sent.append((system, payload))
        if self.error:
            raise self.error
        return self.reply, json.dumps(self.reply, ensure_ascii=False)


class Chat(unittest.TestCase):
    def test_one_call_per_stage_with_view_task_and_schema(self):
        reply = mock_output("S2")
        transport = FakeTransport(reply)
        result = ChatModel(transport).complete("S2", view("S2", upstream={"S1": parse_output("S1", mock_output("S1"), REG, EV).product}),
                                               stage_schema("S2", REG))
        self.assertEqual((result.output, result.reasoning), (reply, "想了一下"))
        system, payload = transport.sent[0]
        self.assertTrue(system.endswith(STAGE_PROMPTS["S2"]))
        self.assertEqual(payload["stage"], "S2")
        self.assertEqual(payload["output_schema"], stage_schema("S2", REG))
        self.assertEqual(payload["view"]["task"]["stage"], "S2")
        self.assertIsInstance(payload["view"]["upstream"]["S1"]["hypotheses"], list)       # products sent as data
        json.dumps(payload, ensure_ascii=False)

    def test_failures_raise_and_never_fall_back(self):
        bad = mock_output("S1")
        del bad["product"]
        with self.assertRaisesRegex(I.ModelError, "missing product"):
            ChatModel(FakeTransport(bad)).complete("S1", view("S1"), stage_schema("S1", REG))
        with self.assertRaisesRegex(I.ModelError, "connection failed"):
            ChatModel(FakeTransport(error=RuntimeError("Model API connection failed"))).complete(
                "S1", view("S1"), stage_schema("S1", REG))
        with self.assertRaises(I.ModelError):
            ChatModel(FakeTransport({})).complete("S9", view("S1"), {})

    def test_real_transport_against_a_local_server(self):
        received, reply = [], mock_output("S3")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                message = {"content": json.dumps(reply, ensure_ascii=False), "reasoning_content": "先对照预期"}
                raw = json.dumps({"model": "fake-2026", "choices": [{"message": message}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"ROLE_SIM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "ROLE_SIM_MODEL": "fake", "ROLE_SIM_API_KEY": ""}
            with patch.dict(os.environ, env):
                model = ChatModel(ChatBackend())
                result = model.complete("S3", view("S3"), stage_schema("S3", REG))
            self.assertEqual((result.output, result.reasoning, model.model), (reply, "先对照预期", "fake"))
            self.assertEqual(json.loads(result.raw), reply)
            body = received[0]
            self.assertEqual(body["response_format"], {"type": "json_object"})
            sent = json.loads(body["messages"][1]["content"])
            self.assertEqual((sent["stage"], sent["view"]["self_id"]), ("S3", "c1"))
            self.assertIn(STAGE_PROMPTS["S3"], body["messages"][0]["content"])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_view_payload_is_plain_data(self):
        product = parse_output("S1", mock_output("S1"), REG, EV).product
        data = view_payload(view("S2", upstream={"S1": product}))
        self.assertEqual(data["upstream"]["S1"]["judgment_state"], "settled")
        self.assertEqual(json.loads(json.dumps(data, ensure_ascii=False)), data)


if __name__ == "__main__":
    unittest.main()
