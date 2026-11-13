#!/usr/bin/env python3
"""agy 1.4.6 lifecycle tests: subagent detection, report enforcement, scout layer, escalation,
primary-model list, mitosis coexistence. Payload shapes come from live captures
(generic paths only)."""

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enforce-orchestrator.py"
spec = importlib.util.spec_from_file_location("enforce_orchestrator_agy", SCRIPT)
eo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eo)

PARENT = "6c0f93da-2912-400b-9b41-a46eacc1bfa3"
SCOUT = "11111111-2222-4333-8444-555555555555"
WORKER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
RESEARCH = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"

GOOD = "STATUS: COMPLETED\nFILES_CHANGED: NONE\nVERIFICATION: grep found 2 hits\nNOTES: see file:///home/u/proj/a.py"
FAILED = ("STATUS: FAILED\nFILES_CHANGED: NONE\nVERIFICATION: grep failed\n"
          "NOTES: file:///home/u/brain/scratch/x.log")
BAD = "I looked around and found nothing."
ENV_KEYS = (
    "AGY_ARGON_PIN_SETTINGS", "AGY_ALLOW_NON_ARGON_PRIMARY", "AGY_ARGON_PRIMARY_MODEL",
    "AGY_ARGON_ENFORCE_REPORT", "AGY_ARGON_ESCALATE", "AGY_ARGON_GEMINI_CONFIG_DIR",
    "AGY_ANTIGRAVITY_SETTINGS", "AGY_ARGON_AUTO_ALLOW", "AGY_ARGON_ALLOW_DIRECT_FASTPATH",
)


class AgyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = self.root / "brain"
        self.brain.mkdir()
        self.cfg = self.root / "gemini-config"
        self.cfg.mkdir()
        self._env = dict(os.environ)
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["AGY_ARGON_GEMINI_CONFIG_DIR"] = str(self.cfg)
        self.ws = str(self.root / "proj")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def adir(self, cid):
        d = self.brain / cid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def payload(self, cid, model, **extra):
        p = {"artifactDirectoryPath": str(self.adir(cid)), "conversationId": cid, "modelName": model,
             "workspacePaths": [self.ws], "transcriptPath": str(self.adir(cid) / "transcript.jsonl")}
        p.update(extra)
        return p

    def primary(self, **extra):
        return self.payload(PARENT, "gemini-4-argon-high", invocationNum=1, **extra)

    def sub_payload(self, cid=SCOUT, **extra):
        return self.payload(cid, "gemini-3.5-flash-lite", **extra)

    def register(self, cid, type_name, role="r"):
        d = self.adir(PARENT) / ".system_generated" / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % cid)).write_text(json.dumps({
            "conversationId": cid, "subagentDescriptor": {"typeName": type_name, "role": role},
            "state": "SUBAGENT_STATE_ALIVE", "spawnStepIndex": 18,
            "workspaceUris": ["file://" + self.ws]}), encoding="utf-8")

    def deliver(self, sender, content, mid=None):
        d = self.adir(PARENT) / ".system_generated" / "messages"
        d.mkdir(parents=True, exist_ok=True)
        mid = mid or "msg-%s-%d" % (sender[:4], len(list(d.glob("*.json"))))
        (d / ("%s.json" % mid)).write_text(json.dumps({
            "id": mid, "recipient": PARENT, "sender": sender, "priority": 1, "timestamp": "t",
            "renderDetails": {"messageTitle": "Message from Probe Scout (research)"},
            "content": content, "sourceMetadata": {}}), encoding="utf-8")
        (d / "read.json").write_text(json.dumps({mid: True}), encoding="utf-8")

    def tool(self, base, name, args):
        return dict(base, toolCall={"name": name, "args": dict(args, toolAction="x", toolSummary="y")})

    def send(self, cid, message, recipient=PARENT):
        return self.tool(self.sub_payload(cid), "send_message", {"Message": message, "Recipient": recipient})

    def invoke(self, prompt, type_name="flash-lite-scout"):
        return self.tool(self.primary(), "invoke_subagent",
                         {"Subagents": [{"TypeName": type_name, "Role": "r", "Model": "inherit", "Prompt": prompt}]})

    def state(self, cid):
        return eo.load_state(self.payload(cid, "m"))


class TestPrimaryModelList(unittest.TestCase):
    def test_frontier_by_name_and_id(self):
        for name in ("Gemini 4 Argon (Medium)", "Gemini 4 Argon (High)", "gemini-4-argon-medium",
                     "gemini-4-argon-high", "Claude Opus 5.5 (High)", "claude-opus-5-5-low",
                     "Claude Sonnet 5.5 (Medium)", "claude-sonnet-5-5-medium", "Claude 3 Opus",
                     "Gemini 4 Carbon (Medium)", "Gemini 4 Carbon (High)", "gemini-4-carbon-medium",
                     "gemini-4-carbon-high",
                     "gemini-4-hydrogen-high", "gemini-4-hydrogen-xhigh", "gemini-4-hydrogen",
                     "Gemini 4 Hydrogen (High)", "Gemini 4 Hydrogen (XHigh)",
                     "Gemini 4 Hydrogen (Extra High)", "gemini-pro-orchestrator",
                     "gemini-pro-orchestrator-argon", "Orchestrator (Carbon)", "Orchestrator (Argon)"):
            self.assertTrue(eo.is_argon_tier(name), name)

    def test_not_frontier(self):
        for name in ("Gemini 4 Argon (Low)", "gemini-4-argon-low", "Gemini 4 Carbon (Low)",
                     "gemini-4-carbon-low", "gemini-3.8-flash-high", "gemini-4-hydrogen-low",
                     "gemini-4-hydrogen-medium", "Gemini 4 Hydrogen (Low)", "Gemini 4 Hydrogen (Medium)",
                     "Gemini 3.8 Flash (High)",
                     "GPT-OSS 120B (Medium)", "gpt-oss-120b-medium", "gpt-5", "o3", "deepseek-v4",
                     "grok-3", "gemini-3.1-pro", "gemini-ultra", "claude-3-5-haiku", "", "auto"):
            self.assertFalse(eo.is_argon_tier(name), name)

    def test_primary_model_env_uses_same_check(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        path.write_text(json.dumps({"model": "gemini-3.8-flash-high"}), encoding="utf-8")
        env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        os.environ.update({"AGY_ANTIGRAVITY_SETTINGS": str(path), "AGY_ARGON_PIN_SETTINGS": "1",
                           "AGY_ARGON_PRIMARY_MODEL": "GPT-OSS 120B (Medium)"})
        status, model = eo.check_primary_model_settings()
        self.assertEqual((status, model), ("pinned", "Gemini 4 Argon (High)"))
        os.environ["AGY_ARGON_PRIMARY_MODEL"] = "Claude Sonnet 5.5 (Medium)"
        path.write_text(json.dumps({"model": "gemini-3.8-flash-high"}), encoding="utf-8")
        self.assertEqual(eo.check_primary_model_settings(), ("pinned", "Claude Sonnet 5.5 (Medium)"))

    def test_carbon_is_a_pin_target(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        path.write_text(json.dumps({"model": "gemini-3.8-flash-high"}), encoding="utf-8")
        env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        os.environ.update({"AGY_ANTIGRAVITY_SETTINGS": str(path), "JUST_AGY_PIN_SETTINGS": "1",
                           "JUST_AGY_PRIMARY_MODEL": "Gemini 4 Carbon (High)"})
        self.assertEqual(eo.check_primary_model_settings(), ("pinned", "Gemini 4 Carbon (High)"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["model"], "Gemini 4 Carbon (High)")
        self.assertEqual(eo.DEFAULT_PRIMARY_MODEL, "Gemini 4 Argon (High)")

    def test_allow_non_argon_still_silences(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        path.write_text(json.dumps({"model": "GPT-OSS 120B (Medium)"}), encoding="utf-8")
        env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        os.environ["AGY_ANTIGRAVITY_SETTINGS"] = str(path)
        os.environ["AGY_ALLOW_NON_ARGON_PRIMARY"] = "1"
        self.assertEqual(eo.check_primary_model_settings()[0], "silenced")
        os.environ.pop("AGY_ALLOW_NON_ARGON_PRIMARY")
        self.assertEqual(eo.check_primary_model_settings()[0], "warn")


class TestPreInvocationWarnings(AgyBase):
    def test_warning_names_allowed_models(self):
        p = dict(self.primary(), modelName="gpt-oss-120b-medium")
        res = eo.handle_pre_invocation(p)
        msg = res["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("Gemini 4 Argon or Carbon (Medium or High)", msg)
        self.assertIn("Claude Opus/Sonnet", msg)

    def test_no_warning_for_carbon_id(self):
        p = dict(self.primary(), modelName="gemini-4-carbon-high")
        self.assertEqual(eo.handle_pre_invocation(p), {})

    def test_hydrogen_high_ok_medium_warns(self):
        p = dict(self.primary(), modelName="gemini-4-hydrogen-high")
        self.assertEqual(eo.handle_pre_invocation(p), {})
        p = dict(self.primary(), modelName="gemini-4-hydrogen-medium")
        res = eo.handle_pre_invocation(p)
        self.assertIn("Hydrogen (High or XHigh)", res["injectSteps"][0]["ephemeralMessage"])

    def test_orchestrator_executor_model(self):
        from just_agy_hook import models
        self.assertTrue(models.is_orchestrator_executor("gemini-flash-worker-agy"))
        self.assertTrue(models.is_orchestrator_executor(" GEMINI-FLASH-WORKER-AGY "))
        self.assertFalse(models.is_orchestrator_executor("gemini-3.8-flash-high"))
        self.assertFalse(models.is_orchestrator_executor("flash-worker"))
        self.assertFalse(models.is_frontier_tier("gemini-flash-worker-agy"))

    def test_executor_model_no_warning_but_flash_warns(self):
        p = dict(self.primary(), modelName="gemini-flash-worker-agy")
        self.assertEqual(eo.handle_pre_invocation(p), {})
        p = dict(self.primary(), modelName="gemini-3.8-flash-high")
        self.assertIn("injectSteps", eo.handle_pre_invocation(p))

    def test_no_warning_for_frontier_primary(self):
        self.assertEqual(eo.handle_pre_invocation(self.primary()), {})

    def test_subagent_conversation_gets_no_model_warning(self):
        self.register(SCOUT, "flash-lite-scout")
        self.assertEqual(eo.handle_pre_invocation(self.sub_payload(invocationNum=1)), {})


class TestPassThroughAllow(AgyBase):
    """agy: `{}` DENIES a tool call; {"decision":"allow"} means no objection (prompt still shown)."""

    def test_no_opinion_paths_return_allow_never_empty(self):
        cases = [
            ("run_command", {"CommandLine": "ls", "Cwd": self.ws}),
            ("write_to_file", {"TargetFile": self.ws + "/a.py"}),
            ("view_file", {"AbsolutePath": self.ws + "/a.py"}),
            ("grep_search", {"Query": "x"}),
        ]
        for name, args in cases:
            res = eo.handle_pre_tool_use(self.tool(self.primary(), name, args))
            self.assertNotEqual(res, {}, name)
            self.assertEqual(res.get("decision"), "allow", name)
        self.assertEqual(eo.handle_pre_tool_use(dict(self.primary())).get("decision"), "allow")
        self.assertEqual(eo.handle_pre_tool_use(dict(self.primary(), toolCall={"name": "x", "args": "s"})).get("decision"), "allow")

    def test_main_error_fallback_is_allow(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('{"decision": "allow"} if mode in ("stop", "pre-tool-use")', src)


class TestSubagentDetection(AgyBase):
    def test_detects_via_parent_metadata_and_caches(self):
        self.register(SCOUT, "flash-lite-scout", role="Probe Scout")
        st = {}
        found = eo.lifecycle.detect_subagent(self.sub_payload(), st)
        self.assertEqual(found, (PARENT, "flash-lite-scout", "Probe Scout"))
        # cached: still resolved after the metadata file disappears
        (self.brain / PARENT / ".system_generated" / "subagents" / ("%s.json" % SCOUT)).unlink()
        self.assertEqual(eo.lifecycle.detect_subagent(self.sub_payload(), st), found)
        self.assertIsNone(eo.lifecycle.detect_subagent(self.sub_payload(), {}))

    def test_primary_is_not_a_subagent(self):
        self.register(SCOUT, "flash-lite-scout")
        self.assertIsNone(eo.lifecycle.detect_subagent(self.primary(), {}))

    def test_missing_artifact_dir(self):
        self.assertIsNone(eo.lifecycle.detect_subagent({"conversationId": SCOUT}, {}))

    def test_state_is_cached_in_own_conversation(self):
        self.register(SCOUT, "flash-lite-scout")
        eo.handle_pre_tool_use(self.tool(self.sub_payload(), "view_file", {"AbsolutePath": "/x"}))
        self.assertEqual(self.state(SCOUT)["self_subagent"]["parent"], PARENT)


class TestReportEnforcement(AgyBase):
    def setUp(self):
        super().setUp()
        self.register(WORKER, "flash-worker")

    def test_valid_report_allowed_and_recorded(self):
        res = eo.handle_pre_tool_use(self.send(WORKER, GOOD))
        self.assertEqual(res["decision"], "allow")
        self.assertIs(self.state(WORKER)["report_ok"], True)

    def test_invalid_denied_once_then_allowed(self):
        res = eo.handle_pre_tool_use(self.send(WORKER, BAD))
        self.assertEqual(res["decision"], "deny")
        self.assertIn("STATUS: COMPLETED | BLOCKED | FAILED", res["reason"])
        self.assertIn("FILES_CHANGED", res["reason"])
        self.assertIn("Expected at least 4 header lines", res["reason"])
        res2 = eo.handle_pre_tool_use(self.send(WORKER, BAD))
        self.assertEqual(res2["decision"], "allow")
        self.assertIs(self.state(WORKER)["report_ok"], False)

    def test_denied_then_valid_resend(self):
        eo.handle_pre_tool_use(self.send(WORKER, BAD))
        self.assertEqual(eo.handle_pre_tool_use(self.send(WORKER, GOOD))["decision"], "allow")
        self.assertIs(self.state(WORKER)["report_ok"], True)

    def test_switch_off(self):
        os.environ["AGY_ARGON_ENFORCE_REPORT"] = "0"
        self.assertEqual(eo.handle_pre_tool_use(self.send(WORKER, BAD))["decision"], "allow")
        self.assertIs(self.state(WORKER)["report_ok"], False)

    def test_other_recipient_not_validated(self):
        res = eo.handle_pre_tool_use(self.send(WORKER, BAD, recipient=RESEARCH))
        self.assertEqual(res["decision"], "allow")

    def test_primary_message_keeps_scout_guard(self):
        self.register(SCOUT, "flash-lite-scout")
        p = self.primary()
        st = eo.load_state(p)
        st["subagents"][SCOUT] = {"model": "flash_lite", "type": "flash-lite-scout"}
        eo.save_state(p, st)
        res = eo.handle_pre_tool_use(self.tool(p, "send_message", {"Recipient": SCOUT, "Message": "Fix the bug in a.py"}))
        self.assertEqual(res["decision"], "deny")


class TestScoutReadOnly(AgyBase):
    def test_scout_blocked_tools(self):
        self.register(SCOUT, "flash-lite-scout")
        for name in ("write_to_file", "replace_file_content", "multi_replace_file_content",
                     "notebook_edit", "run_command"):
            res = eo.handle_pre_tool_use(self.tool(self.sub_payload(), name, {"TargetFile": "/x", "CommandLine": "ls"}))
            self.assertEqual(res["decision"], "deny", name)
            self.assertIn("BLOCKED", res["reason"])

    def test_scout_reads_still_pass(self):
        self.register(SCOUT, "flash-lite-scout")
        res = eo.handle_pre_tool_use(self.tool(self.sub_payload(), "view_file", {"AbsolutePath": "/x"}))
        self.assertEqual(res["decision"], "allow")

    def test_other_types_unaffected(self):
        self.register(RESEARCH, "research")
        self.register(WORKER, "flash-worker")
        for cid in (RESEARCH, WORKER):
            res = eo.handle_pre_tool_use(self.tool(self.sub_payload(cid), "run_command", {"CommandLine": "ls"}))
            self.assertEqual(res["decision"], "allow")

    def test_primary_run_command_unaffected(self):
        self.register(SCOUT, "flash-lite-scout")
        res = eo.handle_pre_tool_use(self.tool(self.primary(), "run_command", {"CommandLine": "ls"}))
        self.assertEqual(res["decision"], "allow")


class TestFlatInvokeSubagent(AgyBase):
    """agy 1.4.6: one flat invoke_subagent call per subagent (no Subagents list)."""

    def flat(self, prompt, type_name="flash-lite-scout", role="Mul Scout"):
        return self.tool(self.primary(), "invoke_subagent",
                         {"Prompt": prompt, "Role": role, "TypeName": type_name})

    def test_scout_lookup_stays_flash_lite_with_model(self):
        res = eo.handle_pre_tool_use(self.flat("Find where foo is defined."))
        self.assertEqual(res["decision"], "allow")
        self.assertEqual(res["overwrite"]["Model"], "flash_lite")
        self.assertNotIn("TypeName", res["overwrite"])

    def test_scout_edit_task_becomes_flash_worker(self):
        res = eo.handle_pre_tool_use(self.flat("Add a function sub(a, b) to src/calc.py."))
        self.assertEqual(res["overwrite"]["Model"], "flash")
        self.assertEqual(res["overwrite"]["TypeName"], "flash-worker")

    def test_role_label_does_not_upgrade_lookup(self):
        res = eo.handle_pre_tool_use(self.flat("Find where foo is defined.", role="Add Scout"))
        self.assertEqual(res["overwrite"]["Model"], "flash_lite")
        self.assertNotIn("TypeName", res["overwrite"])

    def test_role_lookup_with_edit_prompt_upgrades(self):
        res = eo.handle_pre_tool_use(self.flat("Add a function sub(a, b) to src/calc.py.", role="Lookup"))
        self.assertEqual(res["overwrite"]["Model"], "flash")
        self.assertEqual(res["overwrite"]["TypeName"], "flash-worker")

    def test_decide_subagent_ignores_role(self):
        d = eo.decide_subagent({"TypeName": "flash-lite-scout", "Role": "Add Scout",
                                "Prompt": "Find where foo is defined."})
        self.assertEqual(d.tier, "flash_lite")
        d = eo.decide_subagent({"TypeName": "flash-lite-scout", "Role": "Lookup",
                                "Prompt": "Add a function sub(a, b) to src/calc.py."})
        self.assertEqual(d.tier, "flash")

    def test_report_format_appended_to_prompt(self):
        res = eo.handle_pre_tool_use(self.flat("Find where foo is defined."))
        self.assertTrue(res["overwrite"]["Prompt"].startswith("Find where foo is defined."))
        self.assertIn("STATUS:", res["overwrite"]["Prompt"])
        self.assertIn("NOTES:", res["overwrite"]["Prompt"])

    def test_armed_escalation_upgrades_next_flat_call_once(self):
        self.register(SCOUT, "flash-lite-scout", role="Probe Scout")
        eo.save_state(self.primary(), {"seen_messages": []})
        self.deliver(SCOUT, FAILED)
        res = eo.handle_pre_tool_use(self.flat("Find where foo is defined."))
        self.assertEqual((res["overwrite"]["Model"], res["overwrite"]["TypeName"]), ("flash", "flash-worker"))
        self.assertIn("Probe Scout", res["reason"])
        self.assertNotIn("escalation", self.state(PARENT))
        res2 = eo.handle_pre_tool_use(self.flat("Find where bar is defined."))
        self.assertEqual(res2["overwrite"]["Model"], "flash_lite")

    def test_each_flat_call_counts_as_one_escalation_call(self):
        self.register(SCOUT, "flash-lite-scout", role="Probe Scout")
        eo.save_state(self.primary(), {"seen_messages": []})
        self.deliver(SCOUT, FAILED)
        eo.handle_pre_invocation(self.primary())
        heavy = "Implement the feature and edit config.py then run the tests across files."
        for left in (2, 1):
            eo.handle_pre_tool_use(self.flat(heavy, "flash-worker"))
            self.assertEqual(self.state(PARENT)["escalation"]["calls_left"], left)
        eo.handle_pre_tool_use(self.flat(heavy, "flash-worker"))
        self.assertNotIn("escalation", self.state(PARENT))

    def test_empty_prompt_denied(self):
        res = eo.handle_pre_tool_use(self.flat("   "))
        self.assertEqual(res["decision"], "deny")
        self.assertIn("empty Prompt", res["reason"])
        self.assertIn("Mul Scout", res["reason"])

    def test_pending_spawns_recorded(self):
        eo.handle_pre_tool_use(self.flat("Find where foo is defined."))
        eo.handle_pre_tool_use(self.flat("Add a function sub(a, b) to src/calc.py.", role="Sub Adder"))
        spawns = self.state(PARENT)["pending_spawns"]
        self.assertEqual([(s["role"], s["type"], s["model"]) for s in spawns],
                         [("Mul Scout", "flash-lite-scout", "flash_lite"),
                          ("Sub Adder", "flash-worker", "flash")])


class TestMessageScanAndEscalation(AgyBase):
    def setUp(self):
        super().setUp()
        self.register(SCOUT, "flash-lite-scout", role="Probe Scout")
        self.register(WORKER, "flash-worker", role="Builder")
        # Not a first scan: a first scan with no spawns only seeds seen_messages.
        eo.save_state(self.primary(), {"seen_messages": []})

    def test_scout_failure_arms_escalation_and_invoke_rewrites_once(self):
        self.deliver(SCOUT, FAILED)
        eo.handle_pre_invocation(self.primary())
        st = self.state(PARENT)
        self.assertEqual(st["escalation"]["to"], "flash")
        self.assertEqual(st["subagents"][SCOUT]["status"], "FAILED")
        self.assertIs(st["subagents"][SCOUT]["report_ok"], True)
        res = eo.handle_pre_tool_use(self.invoke("Find where foo is defined."))
        item = res["overwrite"]["Subagents"][0]
        self.assertEqual((item["Model"], item["TypeName"]), ("flash", "flash-worker"))
        self.assertIn("Probe Scout", res["reason"])
        self.assertNotIn("escalation", self.state(PARENT))
        self.assertTrue(eo.decide_subagent({"TypeName": "flash-lite-scout", "Prompt": "Find where foo is defined."}).tier == "flash_lite")
        res2 = eo.handle_pre_tool_use(self.invoke("Find where bar is defined."))
        self.assertEqual(res2["overwrite"]["Subagents"][0]["Model"], "flash_lite")

    def test_first_scan_seeds_old_reports_then_new_failure_escalates(self):
        st = self.state(PARENT)
        st.pop("seen_messages")  # fresh state: plugin enabled mid-conversation
        eo.save_state(self.primary(), st)
        self.deliver(SCOUT, FAILED, mid="old1")
        eo.handle_pre_invocation(self.primary())
        st = self.state(PARENT)
        self.assertNotIn("escalation", st)
        self.assertIn("old1", st["seen_messages"])
        self.deliver(SCOUT, FAILED, mid="new1")
        eo.handle_pre_invocation(self.primary())
        st = self.state(PARENT)
        self.assertEqual(st["escalation"]["to"], "flash")
        self.assertIn("new1", st["seen_messages"])

    def test_blocked_also_escalates_and_scan_in_invoke_without_preinvocation(self):
        self.deliver(SCOUT, FAILED.replace("FAILED", "BLOCKED"))
        res = eo.handle_pre_tool_use(self.invoke("Find where foo is defined."))
        self.assertEqual(res["overwrite"]["Subagents"][0]["Model"], "flash")

    def test_completed_scout_and_failed_worker_do_not_escalate(self):
        self.deliver(SCOUT, GOOD)
        self.deliver(WORKER, FAILED)
        eo.handle_pre_invocation(self.primary())
        self.assertNotIn("escalation", self.state(PARENT))

    def test_messages_seen_only_once(self):
        self.deliver(SCOUT, FAILED, mid="m1")
        eo.handle_pre_invocation(self.primary())
        st = self.state(PARENT)
        self.assertIn("m1", st["seen_messages"])
        st.pop("escalation")
        eo.save_state(self.primary(), st)
        eo.handle_pre_invocation(self.primary())
        self.assertNotIn("escalation", self.state(PARENT))

    def test_expires_after_three_non_scout_calls(self):
        self.deliver(SCOUT, FAILED)
        eo.handle_pre_invocation(self.primary())
        heavy = "Implement the feature and edit config.py then run the tests across files."
        for _ in range(3):
            res = eo.handle_pre_tool_use(self.invoke(heavy, "flash-worker"))
            self.assertNotIn("escalation", res.get("reason", ""))
        self.assertNotIn("escalation", self.state(PARENT))
        res = eo.handle_pre_tool_use(self.invoke("Find where foo is defined."))
        self.assertEqual(res["overwrite"]["Subagents"][0]["Model"], "flash_lite")

    def test_expires_after_30_minutes(self):
        self.deliver(SCOUT, FAILED)
        eo.handle_pre_invocation(self.primary())
        st = self.state(PARENT)
        st["escalation"]["ts"] = time.time() - 31 * 60
        eo.save_state(self.primary(), st)
        res = eo.handle_pre_tool_use(self.invoke("Find where foo is defined."))
        self.assertEqual(res["overwrite"]["Subagents"][0]["Model"], "flash_lite")

    def test_switch_off(self):
        os.environ["AGY_ARGON_ESCALATE"] = "0"
        self.deliver(SCOUT, FAILED)
        eo.handle_pre_invocation(self.primary())
        self.assertNotIn("escalation", self.state(PARENT))
        res = eo.handle_pre_tool_use(self.invoke("Find where foo is defined."))
        self.assertEqual(res["overwrite"]["Subagents"][0]["Model"], "flash_lite")

    def test_state_free_decide_subagent_unchanged(self):
        d = eo.decide_subagent({"TypeName": "flash-lite-scout", "Prompt": "Find where foo is defined."})
        self.assertEqual(d.tier, "flash_lite")
        self.assertNotEqual(d.rule, "escalation")

    def test_invalid_report_notice_once_per_subagent(self):
        self.deliver(WORKER, BAD, mid="m1")
        res = eo.handle_pre_invocation(self.primary())
        msgs = [s["ephemeralMessage"] for s in res["injectSteps"]]
        self.assertEqual(len(msgs), 1)
        self.assertIn("Builder", msgs[0])
        self.assertIs(self.state(PARENT)["subagents"][WORKER]["report_ok"], False)
        self.deliver(WORKER, BAD, mid="m2")
        self.assertEqual(eo.handle_pre_invocation(self.primary()), {})

    def test_valid_report_gives_no_notice(self):
        self.deliver(WORKER, GOOD)
        self.assertEqual(eo.handle_pre_invocation(self.primary()), {})


class TestCoexistence(AgyBase):
    def plugin(self, name="mitosis-orchestrator", dirname="mitosis"):
        d = self.cfg / "plugins" / dirname
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")

    def start_msg(self, payload=None):
        res = eo.handle_session_start(payload if payload is not None else self.primary())
        return res["injectSteps"][0]["systemMessage"]["systemMessage"] if res else ""

    def test_warns_when_installed_and_enabled(self):
        self.plugin()
        msg = self.start_msg()
        self.assertIn("agy plugin disable mitosis-orchestrator", msg)
        self.assertIn("rewrite the same subagents", msg)
        self.assertIn("Subagent pinning is active", msg)

    def test_enabled_explicitly(self):
        self.plugin()
        (self.cfg / "config.json").write_text(json.dumps({"plugins": {"mitosis-orchestrator": {"enabled": True}}}))
        self.assertIn("agy plugin disable", self.start_msg())

    def test_silent_when_disabled(self):
        self.plugin()
        (self.cfg / "config.json").write_text(json.dumps({"plugins": {"mitosis-orchestrator": {"enabled": False}}}))
        self.assertNotIn("mitosis", self.start_msg())

    def test_silent_when_not_installed_or_other_name(self):
        self.assertNotIn("mitosis", self.start_msg())
        self.plugin(name="something-else")
        self.assertNotIn("mitosis", self.start_msg())

    def test_subagent_session_start_is_empty(self):
        self.plugin()
        self.register(SCOUT, "flash-lite-scout")
        self.assertEqual(eo.handle_session_start(self.sub_payload()), {})


class TestTurnNudge(AgyBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("JUST_AGY_TURN_NUDGE_SECONDS", None)
        os.environ.pop("AGY_ARGON_TURN_NUDGE_SECONDS", None)
        self.now = 1000.0
        self._orig_time = eo.handlers.time.time
        eo.handlers.time.time = lambda: self.now
        eo.save_state(self.primary(), {"seen_messages": [], "subagents": {WORKER: {"role": "w", "spawned_at": 1000.0}}})

    def tearDown(self):
        eo.handlers.time.time = self._orig_time
        super().tearDown()

    def run_at(self, t, user="do the task", model="gemini-4-argon-high", base=None):
        self.now = 1000.0 + t
        p = base or self.payload(PARENT, model, invocationNum=1, lastUserInput=user)
        return eo.handle_pre_invocation(p)

    def texts(self, res):
        return [s["ephemeralMessage"] for s in res.get("injectSteps", []) if "ephemeralMessage" in s]

    def test_below_threshold_no_nudge(self):
        self.run_at(0)
        self.assertEqual(self.run_at(299), {})

    def test_nudge_once_with_pending_subagent(self):
        self.run_at(0)
        out = self.texts(self.run_at(301))
        self.assertEqual(len(out), 1)
        self.assertIn("5 min since this request and you have delegated work (1 subagent(s) still pending).", out[0])
        self.assertIn("do not dispatch new research or follow-ups", out[0])
        self.assertIn("Write the deliverable now", out[0])
        self.assertEqual(self.run_at(400), {})
        self.assertTrue(self.state(PARENT)["turn_anchor"]["nudged"])

    def test_new_user_input_resets(self):
        self.run_at(0)
        self.assertEqual(len(self.texts(self.run_at(301))), 1)
        self.assertEqual(self.run_at(310, user="another task"), {})
        self.assertEqual(self.run_at(500, user="another task"), {})  # dispatched under the old anchor only
        st = self.state(PARENT)
        st["subagents"][WORKER]["spawned_at"] = 1315.0
        eo.save_state(self.primary(), st)
        self.assertEqual(len(self.texts(self.run_at(611, user="another task"))), 1)

    def test_invocation_num_reset_does_not_reanchor(self):
        self.run_at(0)
        p = self.payload(PARENT, "gemini-4-argon-high", invocationNum=0, lastUserInput="do the task")
        self.assertEqual(len(self.texts(self.run_at(301, base=p))), 1)

    def test_env_zero_disables_and_invalid_uses_default(self):
        os.environ["JUST_AGY_TURN_NUDGE_SECONDS"] = "0"
        self.run_at(0)
        self.assertEqual(self.run_at(10000), {})
        os.environ["JUST_AGY_TURN_NUDGE_SECONDS"] = "-5"
        self.assertEqual(self.run_at(10001), {})
        os.environ["JUST_AGY_TURN_NUDGE_SECONDS"] = "abc"
        self.assertEqual(len(self.texts(self.run_at(10002))), 1)

    def test_env_custom_threshold(self):
        os.environ["JUST_AGY_TURN_NUDGE_SECONDS"] = "60"
        self.run_at(0)
        self.assertEqual(len(self.texts(self.run_at(61))), 1)

    def test_all_reported_still_nudges_once(self):
        st = self.state(PARENT)
        st["subagents"][WORKER]["status"] = "COMPLETED"
        eo.save_state(self.primary(), st)
        self.run_at(0)
        out = self.texts(self.run_at(900))
        self.assertEqual(len(out), 1)
        self.assertIn("you have delegated work. Write the deliverable now", out[0])
        self.assertNotIn("pending", out[0])
        self.assertEqual(self.run_at(950), {})

    def test_no_subagent_dispatched_no_nudge(self):
        eo.save_state(self.primary(), {"seen_messages": []})
        self.run_at(0)
        self.assertEqual(self.run_at(900), {})

    def test_subagent_from_previous_anchor_no_nudge(self):
        self.run_at(0, user="old task")
        st = self.state(PARENT)
        st["subagents"][WORKER]["spawned_at"] = 1000.0
        eo.save_state(self.primary(), st)
        self.run_at(10, user="new task")  # new anchor starts at 1010
        self.assertEqual(self.run_at(400, user="new task"), {})

    def test_pending_spawn_counts_as_dispatched(self):
        eo.save_state(self.primary(), {"seen_messages": [], "pending_spawns": [{"role": "w", "spawned_at": 1005.0}]})
        self.run_at(0)
        out = self.texts(self.run_at(301))
        self.assertIn("(1 subagent(s) still pending)", out[0])

    def test_subagent_payload_no_nudge(self):
        self.run_at(0)
        self.register(WORKER, "flash-worker")
        self.now = 2000.0
        res = eo.handle_pre_invocation(self.payload(WORKER, "gemini-3.5-flash", invocationNum=1, lastUserInput="do the task"))
        self.assertEqual(res, {})

    def test_time_backwards_no_nudge_and_reanchors(self):
        self.run_at(0)
        self.assertEqual(self.run_at(-50), {})
        self.assertEqual(self.run_at(200), {})
        self.assertEqual(len(self.texts(self.run_at(251))), 1)

    def test_composes_with_model_warning(self):
        self.run_at(0, model="gemini-3.5-flash")
        p = self.payload(PARENT, "gemini-3.5-flash", invocationNum=2, lastUserInput="do the task")
        out = self.texts(self.run_at(301, base=p))
        self.assertEqual(len(out), 2)
        self.assertTrue(any("delegated work" in m for m in out))
        self.assertTrue(any("not an allowed primary model" in m for m in out))


class TestPolicyText(unittest.TestCase):
    def test_policy_notice_routing(self):
        n = eo.POLICY_NOTICE
        self.assertNotIn("web/docs research and edits go to `flash`", n)
        self.assertIn("Do sequential web/docs research and tracing yourself (search_web, read_file)", n)
        self.assertIn("do not wait on a single subagent", n)
        self.assertIn("write it as soon as you have an evidence-backed answer", n)


class TestPrimaryOnlyGuards(AgyBase):
    def test_subagent_not_subject_to_primary_guards(self):
        self.register(WORKER, "flash-worker")
        base = self.sub_payload(WORKER)
        for i in range(6):  # primary limit is 3 edits per turn
            res = eo.handle_pre_tool_use(self.tool(base, "write_to_file", {"TargetFile": "%s/f%d.py" % (self.ws, i)}))
            self.assertEqual(res["decision"], "allow", i)
        res = eo.handle_pre_tool_use(self.tool(base, "view_file", {"AbsolutePath": "/home/u/proj/run.log"}))
        self.assertEqual(res["decision"], "allow")

    def test_primary_still_limited(self):
        base = self.primary()
        out = [eo.handle_pre_tool_use(self.tool(base, "write_to_file", {"TargetFile": "%s/f%d.py" % (self.ws, i)}))["decision"]
               for i in range(5)]
        self.assertEqual(out[-1], "deny")

    def test_agy15_path_arg_budget_counts_per_user_turn(self):
        # agy 1.5: write_file/edit_file carry `Path`; PreInvocation fires on every model call.
        out = []
        for n in range(4):
            base = self.payload(PARENT, "gemini-4-argon-high", invocationNum=n)
            eo.handle_pre_invocation(base)
            out.append(eo.handle_pre_tool_use(self.tool(base, "write_file", {"Path": "%s/g%d.py" % (self.ws, n)}))["decision"])
        self.assertEqual(out, ["allow", "allow", "allow", "deny"])
        base = self.payload(PARENT, "gemini-4-argon-high", invocationNum=0)
        eo.handle_pre_invocation(base)  # new user turn resets the budget
        res = eo.handle_pre_tool_use(self.tool(base, "edit_file", {"Path": "%s/h.py" % self.ws}))
        self.assertEqual(res["decision"], "allow")

    def test_fastpath_off_flag_covers_path_arg(self):
        os.environ["JUST_AGY_ALLOW_DIRECT_FASTPATH"] = "1"
        for n in range(5):
            base = self.payload(PARENT, "gemini-4-argon-high", invocationNum=n)
            res = eo.handle_pre_tool_use(self.tool(base, "write_file", {"Path": "%s/k%d.py" % (self.ws, n)}))
            self.assertEqual(res["decision"], "allow")

    def test_subagent_stop_has_no_branch_reminder(self):
        self.register(WORKER, "flash-worker")
        p = dict(self.sub_payload(WORKER), fullyIdle=True, terminationReason="", executionNum=1)
        st = eo.load_state(p)
        st["subagents"]["x"] = {"active_branch": True, "role": "w"}
        eo.save_state(p, st)
        self.assertEqual(eo.handle_stop(p), {"decision": "allow"})


if __name__ == "__main__":
    unittest.main()
