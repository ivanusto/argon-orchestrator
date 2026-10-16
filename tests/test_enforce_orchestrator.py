#!/usr/bin/env python3
"""Unit tests for scripts/enforce-orchestrator.py.  Run: python3 -m unittest discover tests"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enforce-orchestrator.py"
spec = importlib.util.spec_from_file_location("enforce_orchestrator", SCRIPT)
eo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eo)

CID = "6c0f93da-2912-400b-9b41-a46eacc1bfa3"
SCOUT_CID = "11111111-2222-4333-8444-555555555555"
WORKER_CID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def sub(prompt, type_name="research", model="inherit", workspace="inherit", role=None):
    d = {"TypeName": type_name, "Model": model, "Workspace": workspace, "Prompt": prompt}
    if role:
        d["Role"] = role
    return d


# (expected tier, subagent) -- the first three rows are the 1.1.0 misroutes.
CLASSIFIER_TABLE = [
    ("flash", sub("Find the root cause of the flaky test in the auth module and trace the call graph across files.")),
    ("flash", sub("Refactor the test helper. Quick lookup first.")),
    ("flash", sub("Implement the retry logic across three modules and run pytest.", model="flash_lite")),
    ("flash_lite", sub("Locate where the config loader is defined.")),
    ("flash_lite", sub("grep for TODO markers under src/ and list the files.")),
    ("flash_lite", sub("Check git log for the last change to settings.py.")),
    ("flash_lite", sub("Summarize the file README.md structure.")),
    ("flash_lite", sub("Return the version string from package.json.", model="flash_lite")),
    ("flash", sub("Research the pagination API in the docs for stripe and summarise the parameters.")),
    ("flash", sub("Summarize README.md", type_name="self")),
    ("flash", sub("List files in src/", workspace="branch")),
    ("flash", sub("x" * 600, model="pro")),
    ("flash", sub("Look up where parse_token is defined.", type_name="flash-worker")),
    ("flash_lite", sub("Look up where parse_token is defined.", type_name="flash-lite-scout")),
    ("flash", sub("Fix the off-by-one in parse_token.", type_name="flash-lite-scout")),
    ("flash", sub("Debug why the login test fails.", model="flash_lite", workspace="inherit")),
    ("flash_lite", sub("Which file defines UserRepository?")),
]


class TestClassifier(unittest.TestCase):
    def test_table(self):
        for expected, item in CLASSIFIER_TABLE:
            with self.subTest(prompt=item["Prompt"][:60], type=item["TypeName"], model=item["Model"]):
                self.assertEqual(eo.classify_subagent_tier(item), expected)

    def test_scout_with_mutation_is_upgraded_to_worker(self):
        model, type_override, _ = eo.classify_subagent(sub("Edit config.py and remove the dead flag.", type_name="flash-lite-scout"))
        self.assertEqual((model, type_override), ("flash", "flash-worker"))

    def test_bare_common_words_do_not_force_lite(self):
        # "find", "status", "count", "version" alone used to trigger flash_lite in 1.1.0
        model, _, reason = eo.classify_subagent(sub("Find and fix every place where the status count is wrong across files."))
        self.assertEqual(model, "flash")
        self.assertIn("mutation", reason)


class HookBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.payload = {
            "conversationId": CID,
            "artifactDirectoryPath": self.tmp.name,
            "modelName": "Gemini 4 Argon (Medium)",
            "invocationNum": 1,
            "executionNum": 1,
        }
        self._env = dict(os.environ)
        for k in ("AGY_ARGON_PIN_SETTINGS", "AGY_ALLOW_NON_ARGON_PRIMARY", "AGY_ARGON_PRIMARY_MODEL"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def tool(self, name, args, **extra):
        p = dict(self.payload, toolCall={"name": name, "args": args})
        p.update(extra)
        return p

    def write_transcript(self, *cids):
        path = Path(self.tmp.name) / "transcript.jsonl"
        lines = [json.dumps({"tool": "invoke_subagent", "result": {"subagents": [{"conversationId": c} for c in cids]}})]
        lines.append(json.dumps({"artifact": {"conversationId": "99999999-9999-4999-8999-999999999999"}}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.payload["transcriptPath"] = str(path)


class TestInvokeSubagent(HookBase):
    def test_rewrites_inherit_and_records_pending(self):
        res = eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [
            sub("Locate where the config loader is defined."),
            sub("Implement the retry logic and run pytest.", workspace="branch"),
        ]}))
        self.assertEqual(res["decision"], "allow")
        models = [s["Model"] for s in res["overwrite"]["Subagents"]]
        self.assertEqual(models, ["flash_lite", "flash"])
        state = eo.load_state(self.payload)
        self.assertEqual(len(state["pending_spawns"]), 2)
        self.assertTrue(state["pending_spawns"][1]["active_branch"])

    def test_scout_type_with_edit_prompt_becomes_worker(self):
        res = eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [
            sub("Patch the parser and run the tests.", type_name="flash-lite-scout", model="flash_lite")]}))
        out = res["overwrite"]["Subagents"][0]
        self.assertEqual((out["Model"], out["TypeName"]), ("flash", "flash-worker"))

    def test_correct_call_passes_untouched(self):
        res = eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [
            sub("Locate where the config loader is defined.", type_name="flash-lite-scout", model="flash_lite")]}))
        self.assertEqual(res, {"decision": "allow"})

    def test_empty_prompt_denied(self):
        res = eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [sub("")]}))
        self.assertEqual(res["decision"], "deny")

    def test_failed_invoke_rolls_back_pending(self):
        call = self.tool("invoke_subagent", {"Subagents": [sub("List files in src/", workspace="branch")]})
        eo.handle_pre_tool_use(call)
        eo.handle_post_tool_use(dict(call, error="quota"))
        self.assertEqual(eo.load_state(self.payload)["pending_spawns"], [])


class TestSendMessage(HookBase):
    def spawn_and_sync(self):
        eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [
            sub("Locate parse_token.", type_name="flash-lite-scout", model="flash_lite"),
            sub("Fix parse_token and run pytest.", type_name="flash-worker", model="flash", workspace="branch"),
        ]}))
        self.write_transcript(SCOUT_CID, WORKER_CID)
        eo.handle_post_tool_use(self.tool("invoke_subagent", {}))
        state = eo.load_state(self.payload)
        self.assertEqual(state["subagents"][SCOUT_CID]["model"], "flash_lite")
        self.assertEqual(state["subagents"][WORKER_CID]["model"], "flash")
        self.assertNotIn("99999999-9999-4999-8999-999999999999", state["subagents"])

    def test_mutation_to_scout_denied(self):
        self.spawn_and_sync()
        res = eo.handle_pre_tool_use(self.tool("send_message", {"Recipient": SCOUT_CID, "Message": "Now edit parser.py to add the check."}))
        self.assertEqual(res["decision"], "deny")

    def test_multistep_to_scout_denied(self):
        self.spawn_and_sync()
        msg = "TASK: x\n1. Open a.\n2. Open b.\n3. Open c.\n4. Open d."
        res = eo.handle_pre_tool_use(self.tool("send_message", {"Recipient": SCOUT_CID, "Message": msg}))
        self.assertEqual(res["decision"], "deny")

    def test_lookup_to_scout_and_edit_to_worker_allowed(self):
        self.spawn_and_sync()
        self.assertEqual(eo.handle_pre_tool_use(self.tool("send_message", {"Recipient": SCOUT_CID, "Message": "Also locate parse_header."}))["decision"], "allow")
        self.assertEqual(eo.handle_pre_tool_use(self.tool("send_message", {"Recipient": WORKER_CID, "Message": "Now edit parser.py too."}))["decision"], "allow")

    def test_unknown_recipient_allowed(self):
        res = eo.handle_pre_tool_use(self.tool("send_message", {"Recipient": "unknown", "Message": "Edit everything."}))
        self.assertEqual(res["decision"], "allow")


class TestRunWorkflowAndDefine(HookBase):
    def test_workflow_without_model_denied(self):
        script = 'r = await agent("scan modules")\nw = await agent("fix", model="flash")'
        res = eo.handle_pre_tool_use(self.tool("run_workflow", {"script": script}))
        self.assertEqual(res["decision"], "deny")
        self.assertIn("#1", res["reason"])

    def test_workflow_with_inherit_denied_and_valid_allowed(self):
        bad = 'agent("x", model="inherit")'
        good = 'agent("x", model="flash_lite")\nagent(prompt=f"y({z})", model=\'flash\')\nagent("v", model=TIER)'
        self.assertEqual(eo.handle_pre_tool_use(self.tool("run_workflow", {"script": bad}))["decision"], "deny")
        self.assertEqual(eo.handle_pre_tool_use(self.tool("run_workflow", {"script": good}))["decision"], "allow")

    def test_define_subagent_forces_no_nesting(self):
        res = eo.handle_pre_tool_use(self.tool("define_subagent", {"name": "x", "enable_subagent_tools": True}))
        self.assertEqual(res["overwrite"], {"enable_subagent_tools": False})


class TestStopAndKill(HookBase):
    def spawn_branch(self):
        eo.handle_pre_tool_use(self.tool("invoke_subagent", {"Subagents": [
            sub("Fix parse_token and run pytest.", type_name="flash-worker", model="flash", workspace="branch")]}))
        self.write_transcript(WORKER_CID)
        eo.handle_post_tool_use(self.tool("invoke_subagent", {}))

    def test_stop_reminds_once_then_allows(self):
        self.spawn_branch()
        first = eo.handle_stop(dict(self.payload, fullyIdle=True))
        self.assertEqual(first["decision"], "continue")
        self.assertIn(WORKER_CID, first["reason"])
        second = eo.handle_stop(dict(self.payload, fullyIdle=True))
        self.assertEqual(second["decision"], "allow")

    def test_stop_allows_after_kill(self):
        self.spawn_branch()
        eo.handle_pre_tool_use(self.tool("manage_subagents", {"Action": "kill", "ConversationIds": [WORKER_CID]}))
        self.assertEqual(eo.handle_stop(dict(self.payload, fullyIdle=True))["decision"], "allow")

    def test_stop_never_blocks_cancel_or_busy(self):
        self.spawn_branch()
        self.assertEqual(eo.handle_stop(dict(self.payload, fullyIdle=False))["decision"], "allow")
        self.assertEqual(eo.handle_stop(dict(self.payload, fullyIdle=True, terminationReason="USER_CANCELED"))["decision"], "allow")


class TestPrimaryModelGuard(HookBase):
    def test_is_argon_tier(self):
        self.assertTrue(eo.is_argon_tier("Gemini 4 Argon (High)"))
        self.assertTrue(eo.is_argon_tier("gemini-3.1-pro-preview"))
        for bad in ("gemini-3-flash", "gemini-flash-lite", "auto", "", "inherit"):
            self.assertFalse(eo.is_argon_tier(bad), bad)

    def test_pre_invocation_warns_once_for_non_argon(self):
        self.assertEqual(eo.handle_pre_invocation(self.payload), {})
        p = dict(self.payload, modelName="gemini-3-flash", invocationNum=2)
        self.assertIn("injectSteps", eo.handle_pre_invocation(p))
        self.assertEqual(eo.handle_pre_invocation(p), {})
        os.environ["AGY_ALLOW_NON_ARGON_PRIMARY"] = "1"
        self.assertEqual(eo.handle_pre_invocation(dict(p, invocationNum=3)), {})

    def _settings(self, model):
        path = Path(self.tmp.name) / "settings.json"
        path.write_text(json.dumps({"model": model, "other": 1}), encoding="utf-8")
        os.environ["AGY_ANTIGRAVITY_SETTINGS"] = str(path)
        return path

    def test_session_start_warns_but_does_not_rewrite_by_default(self):
        path = self._settings("gemini-3-flash")
        res = eo.handle_session_start({})
        msg = res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("WARNING", msg)
        self.assertEqual(json.loads(path.read_text())["model"], "gemini-3-flash")

    def test_session_start_pins_when_opted_in(self):
        path = self._settings("gemini-3-flash")
        os.environ["AGY_ARGON_PIN_SETTINGS"] = "1"
        os.environ["AGY_ARGON_PRIMARY_MODEL"] = "Gemini 4 Argon (Medium)"
        res = eo.handle_session_start({})
        self.assertIn("Restored", res["injectSteps"][0]["systemMessage"]["systemMessage"])
        data = json.loads(path.read_text())
        self.assertEqual(data["model"], "Gemini 4 Argon (Medium)")
        self.assertEqual(data["other"], 1)

    def test_session_start_keeps_user_tier(self):
        self._settings("Gemini 4 Argon (Medium)")
        os.environ["AGY_ARGON_PIN_SETTINGS"] = "1"
        msg = eo.handle_session_start({})["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("Primary model is 'Gemini 4 Argon (Medium)'", msg)


if __name__ == "__main__":
    unittest.main()
