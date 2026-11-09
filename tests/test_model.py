"""IModel (rolesim/model.py) and the stage output schemas (rolesim/schemas.py)."""
import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from rolesim import interfaces as I
from rolesim.state import slots
from rolesim.stage.model import STAGE_PROMPTS, ChatModel, MockModel, view_payload
from rolesim.stage.providers import ChatBackend
from rolesim.role.registry import Registry
from rolesim.stage.schemas import (JUDGMENT_OUTPUTS, PRODUCTS, compact_schema, narrow_evidence, parse_output,
                                   stage_mechanisms, stage_schema, stage_task, validate)
from rolesim.state.chooser import ModelValueChooser
from rolesim.state.store import ConstructStore

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
            branches = schema["properties"]["judgments"]["items"]["anyOf"]
            self.assertEqual([b["properties"]["dm_id"]["const"] for b in branches],
                             [d for d in REG.ids("mechanism") if stage in REG.mechanism(d).stages])
            for b in branches:                                                  # each process: only its own options
                options = REG.mechanism(b["properties"]["dm_id"]["const"]).options
                self.assertEqual(b["properties"]["option"], {"enum": [*options, None]} if options else {"type": "null"})
                if b["properties"]["dm_id"]["const"] in JUDGMENT_OUTPUTS:             # mechanism cards: typed outputs
                    self.assertEqual(b["properties"]["output"], JUDGMENT_OUTPUTS[b["properties"]["dm_id"]["const"]])
            self.assertEqual(("evidence_notes" in schema["properties"]), stage == "S1")
            self.assertNotIn("proposals", schema["properties"])                 # C8: the model never writes state
            self.assertNotIn('"pattern"', json.dumps(schema))                   # xgrammar drops enforcement

    def test_task_carries_options_and_fields(self):
        task = stage_task("S1", REG)
        appraisal = next(m for m in task["mechanisms"] if m["dm_id"] == "dm.affect.primary_secondary_appraisal")
        self.assertEqual(appraisal["options"], list(REG.mechanism("dm.affect.primary_secondary_appraisal").options))
        self.assertEqual(appraisal["hands_to"], ["emotion_update", "self_state_update"])      # goal_update is not S1
        self.assertEqual(appraisal["output_fields"], ["grade", "emotions", "valence", "about"])
        self.assertNotIn("constructs", task)                                   # no construct is the model's to name
        self.assertNotIn("bands", task)
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
            self.assertFalse(hasattr(out, "proposals"))
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
        out["proposals"] = []                                                   # the model may not write state
        with self.assertRaisesRegex(I.StageError, "unexpected field proposals"):
            parse_output("S1", out, REG, EV)

    def test_registry_and_product_rules(self):
        out = mock_output("S1")
        out["judgments"][0]["option"] = "愤怒"
        with self.assertRaisesRegex(I.StageError, "has no option|option: '.*' is not one of the allowed values"):
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
        self.assertEqual(parsed.judgments[0].evidence[0].channel, "hearsay")    # judgments cite the refined evidence
        out["evidence_notes"] = [{"event": "a7#0", "channel": "hearsay", "claim": None}]       # claim unknown: still weaker
        self.assertEqual(parse_output("S1", out, REG, EV).evidence.included, (I.EvidenceRef("a7#0", "hearsay"),))
        out["evidence_notes"] = [{"event": "zz#1", "channel": "inferred", "claim": None}]
        with self.assertRaisesRegex(I.StageError, "has not seen"):
            parse_output("S1", out, REG, EV)
        heard = I.EvidenceSet((I.EvidenceRef("a7#0", "hearsay", "x"),))                    # never stronger: ignored
        out["evidence_notes"] = [{"event": "a7#0", "channel": "witnessed", "claim": None}]
        self.assertEqual(parse_output("S1", out, REG, heard).evidence.included, heard.included)
        own = I.EvidenceSet((I.EvidenceRef("a7#0", "own_action"),))                        # one's own action: ignored
        self.assertEqual(parse_output("S1", out, REG, own).evidence.included, own.included)

    def test_unknown_ids_stay_weak_and_visible_to_the_store(self):
        out = mock_output("S1")
        out["judgments"][1]["evidence"] = ["zz#9"]                             # the belief judgment
        parsed = parse_output("S1", out, REG, EV)
        self.assertEqual(parsed.judgments[1].evidence, (I.EvidenceRef("zz#9", "inferred"),))
        store, key = ConstructStore(REG), ("i1", "c1")
        store.open(key, slots.blank_state(REG, "c1", "snap1"))
        ctx = I.StageContext(I.Phase("a7", 1, "event"), store.snapshot(key), I.StableProfile("snap1", "c1", {}), (), {}, {}, key)
        slots_, skipped = store.draft(ctx, parsed)
        changes, unvalued = ModelValueChooser(REG, MockModel(REG)).choose(ctx, parsed, slots_)
        result = store.commit(ctx, changes, parsed.evidence, skipped, unvalued)
        self.assertIn("evidence_not_visible", [r.error.code for r in result.rejected])


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
            slots_, skipped = store.draft(ctx, out)
            changes, unvalued = ModelValueChooser(REG, MockModel(REG)).choose(ctx, out, slots_)
            result = store.commit(ctx, changes, out.evidence, skipped, unvalued)
            self.assertEqual(result.rejected, (), stage)
            self.assertEqual(result.soft_flags, (), stage)
            self.assertTrue(result.accepted, stage)
        snap = store.snapshot(key)
        self.assertIn("b-a7-1", snap.dynamic["d.cognition.belief"])
        self.assertEqual(snap.dynamic["d.policy.current_strategy"]["dm.conflict.conflict_mode"]["c2"], "合作")
        self.assertIn("m-a7", snap.memory["m.entry"])

    def test_nothing_seen_means_no_judgments(self):
        for stage in KINDS:
            out = parse_output(stage, mock_output(stage, records=()), REG, I.EvidenceSet(()))
            self.assertEqual(out.judgments, (), stage)


class FakeTransport:
    model = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.sent, self.last_reasoning = reply, error, [], "想了一下"

    def chat(self, system, payload, schema=None):
        self.sent.append((system, payload))
        self.schemas = getattr(self, "schemas", []) + [schema]
        if self.error:
            raise self.error
        return self.reply, json.dumps(self.reply, ensure_ascii=False)


class Chat(unittest.TestCase):
    def test_narrow_evidence_limits_ids_and_leaves_the_rest(self):
        schema = stage_schema("S1", REG)
        narrow = narrow_evidence(schema, ["a#1", "b#2", "a#1"])
        props = narrow["properties"]
        self.assertEqual(narrow["$defs"]["evidence"]["items"]["enum"], ["a#1", "b#2"])
        for branch in props["judgments"]["items"]["anyOf"]:
            self.assertEqual(branch["properties"]["evidence"], {"$ref": "#/$defs/evidence"})
        self.assertEqual(props["evidence_notes"]["items"]["properties"]["event"], {"enum": ["a#1", "b#2"]})
        self.assertEqual(props["product"], schema["properties"]["product"])
        self.assertEqual(schema, stage_schema("S1", REG))                    # the original is untouched
        out = mock_output("S1")
        out["evidence_notes"] = [{"event": "上周例会约定", "channel": "hearsay", "claim": "x"}]
        self.assertTrue(any("evidence_notes" in e for e in validate(narrow, out)))
        empty = narrow_evidence(schema, [])["properties"]
        self.assertEqual((narrow_evidence(schema, [])["$defs"]["evidence"]["maxItems"],
                          empty["evidence_notes"]["maxItems"]), (0, 0))

    def test_audience_is_limited_to_the_other_present_ids(self):
        narrow = narrow_evidence(stage_schema("S2", REG), ["a#1"], ["c2", "c3"])
        self.assertEqual(narrow["properties"]["product"]["properties"]["expression"]["properties"]["audience"],
                         {"type": "array", "items": {"enum": ["c2", "c3"]}})
        reply = mock_output("S2")
        reply["product"]["expression"]["audience"] = ["周越"]
        self.assertTrue(any("audience" in e for e in validate(narrow, reply)))
        self.assertEqual(narrow_evidence(stage_schema("S2", REG), ["a#1"])["properties"]["product"],
                         stage_schema("S2", REG)["properties"]["product"])          # no ids given: unchanged
        s1 = narrow_evidence(stage_schema("S1", REG), ["a#1"], ["c2"])           # every "about" too
        appraisal = next(b for b in s1["properties"]["judgments"]["items"]["anyOf"]
                         if b["properties"]["dm_id"]["const"] == "dm.affect.primary_secondary_appraisal")
        self.assertEqual(appraisal["properties"]["output"]["properties"]["about"], {"enum": ["c2", None]})
        hypothesis = s1["properties"]["product"]["properties"]["hypotheses"]["items"]["properties"]["about"]
        self.assertEqual(hypothesis, {"enum": ["c2", None]})
        compact = compact_schema(stage_schema("S2", REG))
        self.assertNotIn("$defs", compact)
        self.assertLess(len(json.dumps(compact, ensure_ascii=False)), 6000)

    def test_an_option_of_another_process_breaks_the_schema(self):
        reply = mock_output("S1")
        self.assertTrue(reply["judgments"])
        reply["judgments"][0]["option"] = "线索解释-不存在"
        self.assertTrue(validate(stage_schema("S1", REG), reply))
        reply["judgments"][0]["option"] = None
        self.assertEqual(validate(stage_schema("S1", REG), reply), [])

    def test_free_text_evidence_fails_before_parsing(self):
        reply = mock_output("S1")
        reply["evidence_notes"] = [{"event": "第三批数据未完成", "channel": "inferred", "claim": None}]
        with self.assertRaisesRegex(I.ModelError, "evidence_notes"):
            ChatModel(FakeTransport(reply)).complete("S1", view("S1"), stage_schema("S1", REG))

    def test_one_call_per_stage_with_view_task_and_schema(self):
        reply = mock_output("S2")
        transport = FakeTransport(reply)
        result = ChatModel(transport).complete("S2", view("S2", upstream={"S1": parse_output("S1", mock_output("S1"), REG, EV).product}),
                                               stage_schema("S2", REG))
        self.assertEqual((result.output, result.reasoning), (reply, "想了一下"))
        system, payload = transport.sent[0]
        self.assertTrue(system.endswith(STAGE_PROMPTS["S2"]))
        self.assertEqual(payload["stage"], "S2")
        ids = [f"{r['action_id']}#{r['order_index']}" for r in payload["view"]["records"]]
        self.assertTrue(ids)
        others = [c for c in payload["view"]["present"] if c != payload["view"]["self_id"]]
        full = narrow_evidence(stage_schema("S2", REG), ids, others)
        self.assertEqual(transport.schemas[0], full)                        # the full schema goes to the decoder
        self.assertEqual(payload["output_schema"], compact_schema(full))    # the prompt carries the short form
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
            sent = json.loads(body["messages"][1]["content"])
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertEqual(compact_schema(body["response_format"]["json_schema"]["schema"]), sent["output_schema"])
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
