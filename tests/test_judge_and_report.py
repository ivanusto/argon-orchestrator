#!/usr/bin/env python3
"""Judge fallback, REPORT FORMAT append and invoke_subagent integration."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enforce-orchestrator.py"
spec = importlib.util.spec_from_file_location("enforce_orchestrator_judge", SCRIPT)
eo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eo)

from argon_hook import judge, report, routing  # noqa: E402  (scripts/ is on sys.path after the load above)

CID = "6c0f93da-2912-400b-9b41-a46eacc1bfa3"
ENV_PREFIXES = ("AGY_ARGON_", "MITOSIS_JUDGE_")
# A scout-typed lookup the rules route with confidence below the 0.9 threshold ("scout-type" is 0.95, so use a default-rule prompt).
LOW_CONFIDENCE_SPEC = {"prompt": "Tell me something about the repository structure and how the pieces fit together " * 3}


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(ENV_PREFIXES):
                os.environ.pop(k, None)
        self._saved_loader = routing._load_judge

    def tearDown(self):
        routing._load_judge = self._saved_loader
        os.environ.clear()
        os.environ.update(self._env)


def fake_opener(body=None, exc=None, seen=None):
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request, timeout))
        if exc is not None:
            raise exc
        return io.BytesIO(json.dumps(body).encode("utf-8"))
    return opener


class FakeJudge(object):
    def __init__(self, verdict=None, exc=None):
        self.verdict, self.exc, self.calls = verdict, exc, 0

    def judge_tier(self, prompt):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.verdict


class TestJudgeModule(EnvCase):
    def test_no_url_means_no_judge(self):
        self.assertIsNone(judge.judge_tier("anything"))

    def test_url_env_prefers_argon_then_mitosis(self):
        seen = []
        body = {"answers": {"tier": {"probabilities": {"scout": 0.1, "worker": 0.9}}}}
        os.environ["MITOSIS_JUDGE_URL"] = "http://127.0.0.1:1/legacy"
        self.assertEqual(judge.judge_tier("x", opener=fake_opener(body, seen=seen)), ("flash", 0.9))
        self.assertTrue(seen[0][0].full_url.endswith("/legacy"))
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/argon"
        judge.judge_tier("x", opener=fake_opener(body, seen=seen))
        self.assertTrue(seen[1][0].full_url.endswith("/argon"))

    def test_error_and_unsure_answers_return_none(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        self.assertIsNone(judge.judge_tier("x", opener=fake_opener(exc=OSError("down"))))
        unsure = {"answers": {"tier": {"probabilities": {"scout": 0.55, "worker": 0.45}}}}
        self.assertIsNone(judge.judge_tier("x", opener=fake_opener(unsure)))
        self.assertIsNone(judge.judge_tier("x", opener=fake_opener({"nope": 1})))

    def test_non_http_scheme_is_refused(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "file:///etc/passwd"
        self.assertIsNone(judge.judge_tier("x"))

    def test_redact_masks_secrets(self):
        out = judge.redact("key sk-ant-abcdef123456 host 203.0.113.7 /home/alice/repo")
        for leaked in ("sk-ant-abcdef123456", "203.0.113.7", "alice"):
            self.assertNotIn(leaked, out)


class TestJudgeFallback(EnvCase):
    def rules_only(self):
        return routing.decide_subagent(dict(LOW_CONFIDENCE_SPEC))

    def test_baseline_is_judgeable_and_low_confidence(self):
        d = self.rules_only()
        self.assertIn(d.rule, routing.JUDGEABLE_RULES)
        self.assertLess(d.confidence, 0.9)

    def test_unset_url_leaves_rule_decision(self):
        fake = FakeJudge(("flash_lite", 0.99))
        routing._load_judge = lambda: fake
        d = self.rules_only()
        self.assertEqual((d.source, fake.calls), ("rule", 0))

    def test_judge_error_falls_back_to_flash(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        routing._load_judge = lambda: FakeJudge(exc=RuntimeError("boom"))
        d = self.rules_only()
        self.assertEqual((d.tier, d.source), ("flash", "default"))
        self.assertIn("judge error RuntimeError", d.reason)

    def test_unsure_judge_falls_back_to_flash(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        routing._load_judge = lambda: FakeJudge(verdict=None)
        d = self.rules_only()
        self.assertEqual((d.tier, d.source), ("flash", "default"))
        self.assertIn("judge unavailable", d.reason)

    def test_judge_verdict_is_used_when_confident(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        routing._load_judge = lambda: FakeJudge(verdict=("flash_lite", 0.95))
        d = self.rules_only()
        self.assertEqual((d.tier, d.source, d.confidence), ("flash_lite", "judge", 0.95))

    def test_threshold_env_gates_the_judge(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        os.environ["AGY_ARGON_JUDGE_THRESHOLD"] = "0.4"  # rule confidence 0.5 is now enough
        fake = FakeJudge(("flash_lite", 0.95))
        routing._load_judge = lambda: fake
        d = self.rules_only()
        self.assertEqual((d.source, fake.calls), ("rule", 0))

    def test_type_rules_are_never_judged(self):
        os.environ["AGY_ARGON_JUDGE_URL"] = "http://127.0.0.1:1/j"
        fake = FakeJudge(("flash_lite", 0.99))
        routing._load_judge = lambda: fake
        d = routing.decide_subagent({"prompt": "Do the thing.", "subagent_type": "flash-worker", "model": "flash"})
        self.assertEqual(fake.calls, 0)
        self.assertEqual(d.tier, "flash")


class TestRoutingEngine(EnvCase):
    def test_type_prefixes_are_stripped(self):
        for prefix in ("argon-orchestrator:", "mitosis-orchestrator:"):
            d = routing.decide_subagent({"prompt": "Edit config.py and remove the dead flag.",
                                         "subagent_type": prefix + "flash-lite-scout"})
            self.assertEqual((d.tier, d.type_override), ("flash", prefix + "flash-worker"))
            d = routing.decide_subagent({"prompt": "Fix the parser bug.", "subagent_type": prefix + "flash-worker"})
            self.assertEqual((d.tier, d.rule), ("flash", "worker-type"))

    def test_worker_downgrade_switch(self):
        spec = {"prompt": "Where is parse_token defined?", "TypeName": "flash-worker"}
        self.assertEqual(routing.decide_subagent(spec).rule, "worker-lookup")
        os.environ["AGY_ARGON_WORKER_DOWNGRADE"] = "0"
        self.assertEqual(routing.decide_subagent(spec).rule, "worker-type")

    def test_scout_without_read_only_evidence_is_unproven(self):
        d = routing.decide_subagent({"prompt": "The config module needs attention", "TypeName": "flash-lite-scout"})
        self.assertEqual((d.tier, d.rule, d.type_override), ("flash", "scout-unproven", "flash-worker"))

    def test_compat_wrappers(self):
        item = {"TypeName": "flash-lite-scout", "Prompt": "Edit config.py and remove the dead flag."}
        self.assertEqual(routing.classify_subagent(item)[:2], ("flash", "flash-worker"))
        self.assertEqual(routing.classify_subagent_tier({"Prompt": "Which file defines UserRepository?"}), "flash_lite")


class TestReportAppend(EnvCase):
    def test_all_four_headers_are_required(self):
        full = "do x\nSTATUS: a\nFILES_CHANGED: b\nVERIFICATION: c\nNOTES: d"
        self.assertTrue(report.prompt_has_report_format(full))
        self.assertFalse(report.prompt_has_report_format(full.replace("NOTES:", "NOTE:")))
        self.assertFalse(report.prompt_has_report_format("REPORT FORMAT: STATUS/FILES/TESTS"))

    def test_block_matches_contract(self):
        for block in (report.REPORT_FORMAT_SCOUT, report.REPORT_FORMAT_WORKER):
            self.assertLessEqual(len(block), report.MAX_SUBAGENT_REPORT_CHARS)
            self.assertIn("up to 10 `- ` bullet lines", block)
            self.assertIn("file:// log URI", block)
            self.assertTrue(report.prompt_has_report_format(block))
        self.assertIn("FILES_CHANGED: NONE", report.REPORT_FORMAT_SCOUT)
        self.assertIn("FILES_CHANGED: <comma-separated paths or NONE>", report.REPORT_FORMAT_WORKER)

    def test_append_picks_block_by_tier_and_is_idempotent(self):
        scout = report.append_report_format("Locate X.", "flash_lite")
        worker = report.append_report_format("Fix X.", "flash")
        self.assertTrue(scout.endswith(report.REPORT_FORMAT_SCOUT))
        self.assertTrue(worker.endswith(report.REPORT_FORMAT_WORKER))
        self.assertEqual(report.append_report_format(scout, "flash_lite"), scout)

    def test_foreign_report_format_is_superseded(self):
        out = report.append_report_format("Fix X.\nREPORT FORMAT: STATUS/FILES/TESTS", "flash")
        self.assertIn(report.REPORT_FORMAT_SUPERSEDE, out)

    def test_strip_report_format_for_routing(self):
        prompt = "Locate parse_token.\n\n" + report.REPORT_FORMAT_WORKER
        self.assertEqual(report.strip_report_format(prompt), "Locate parse_token")


class TestInvokeSubagentAppend(EnvCase):
    def setUp(self):
        EnvCase.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.payload = {"conversationId": CID, "artifactDirectoryPath": self.tmp.name}

    def tearDown(self):
        eo._release_state_locks()
        self.tmp.cleanup()
        EnvCase.tearDown(self)

    def call(self, *subs):
        p = dict(self.payload, toolCall={"name": "invoke_subagent", "args": {"Subagents": list(subs)}})
        return eo.handle_pre_tool_use(p)

    def test_scout_and_worker_get_their_blocks(self):
        res = self.call(
            {"TypeName": "flash-lite-scout", "Model": "flash_lite", "Prompt": "Locate where the config loader is defined."},
            {"TypeName": "flash-worker", "Model": "flash", "Prompt": "Implement the retry logic and run pytest."})
        prompts = [s["Prompt"] for s in res["overwrite"]["Subagents"]]
        self.assertTrue(prompts[0].endswith(report.REPORT_FORMAT_SCOUT))
        self.assertTrue(prompts[1].endswith(report.REPORT_FORMAT_WORKER))
        self.assertIn("appended REPORT FORMAT", res["reason"])
        self.assertEqual(len(eo.load_state(self.payload)["pending_spawns"]), 2)

    def test_prompt_with_all_headers_is_left_alone(self):
        prompt = "Locate X.\n" + report.REPORT_FORMAT_SCOUT
        res = self.call({"TypeName": "flash-lite-scout", "Model": "flash_lite", "Prompt": prompt})
        self.assertEqual(res, {"decision": "allow"})

    def test_switch_disables_append(self):
        os.environ["AGY_ARGON_APPEND_REPORT_FORMAT"] = "0"
        res = self.call({"TypeName": "flash-lite-scout", "Model": "flash_lite",
                         "Prompt": "Locate where the config loader is defined."})
        self.assertEqual(res, {"decision": "allow"})

    def test_lowercase_keys_are_normalised(self):
        res = self.call({"typeName": "flash-lite-scout", "model": "flash_lite", "prompt": "Locate where the config loader is defined."})
        out = res["overwrite"]["Subagents"][0]
        self.assertNotIn("prompt", out)
        self.assertTrue(out["Prompt"].endswith(report.REPORT_FORMAT_SCOUT))


if __name__ == "__main__":
    unittest.main()
