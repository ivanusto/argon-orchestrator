#!/usr/bin/env python3
"""The hook commands must start Python on every platform, through the shell agy uses.

agy runs hooks with `cmd /c` on Windows. The Microsoft Store alias `python3.exe` exits 9009 with
no output, and a python.org install has `python` and `py` but no `python3`.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "enforce-orchestrator.py"
WINDOWS = os.name == "nt"
PAYLOAD = {
    "hook_event_name": "PreToolUse",
    "conversationId": "launcher-test",
    "toolCall": {"name": "view_file", "args": {"AbsolutePath": "x.py"}},
}


def commands(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                yield value
            else:
                yield from commands(value)
    elif isinstance(node, list):
        for item in node:
            yield from commands(item)


def hook_commands():
    return list(commands(json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))))


def run_shell(command, env, stdin):
    if WINDOWS:  # the raw command line, as `cmd /c <command>`
        argv = 'cmd /d /s /c "' + command + '"'
    else:
        argv = ["/bin/sh", "-c", command]
    return subprocess.run(argv, input=stdin, capture_output=True, env=env, cwd=str(ROOT), timeout=60)


class ShimDir:
    """A PATH with only the interpreters a test names; each logs that it ran."""

    def __init__(self, tmp):
        self.dir = Path(tmp) / "bin"
        self.dir.mkdir()
        self.log = Path(tmp) / "ran.log"
        self.tmp = tmp

    def add(self, name, works=True):
        real = sys.executable
        if WINDOWS:
            # `py -3 x` drops the version flag; the others pass everything through.
            args = "%2 %3 %4 %5" if name == "py" else "%*"
            body = f'@echo {name}>>"{self.log}"\r\n'
            body += f'@"{real}" -B {args}\r\n' if works else "@exit /b 9009\r\n"
            (self.dir / (name + ".cmd")).write_text(body, encoding="ascii", newline="")
        else:
            body = f'#!/bin/sh\necho {name} >> "{self.log}"\n'
            if name == "py":
                body += "shift\n"
            body += f'exec "{real}" -B "$@"\n' if works else "exit 127\n"
            path = self.dir / name
            path.write_text(body, encoding="ascii")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return self

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        path = [str(self.dir)]
        if WINDOWS:  # cmd.exe itself; System32 ships no python
            path.append(os.path.join(os.environ["SystemRoot"], "System32"))
        env["PATH"] = os.pathsep.join(path)
        env["TMP"] = env["TEMP"] = env["TMPDIR"] = self.tmp
        return env

    def ran(self):
        if not self.log.exists():
            return []
        return [line.strip() for line in self.log.read_text().splitlines() if line.strip()]


class TestFallbackChain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shims = ShimDir(self._tmp.name)

    def scenario(self, expected_ran):
        proc = run_shell(hook_commands()[0], self.shims.env(), json.dumps(PAYLOAD).encode())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsInstance(json.loads(proc.stdout.decode()), dict)
        self.assertEqual(self.shims.ran(), expected_ran)

    def test_python3_is_tried_first_and_runs_once(self):
        self.shims.add("python3").add("python").add("py")
        self.scenario(["python3"])

    def test_only_python(self):
        self.shims.add("python")
        self.scenario(["python"])

    def test_store_alias_falls_through_to_python(self):
        self.shims.add("python3", works=False).add("python")
        self.scenario(["python3", "python"])

    def test_store_aliases_fall_through_to_py(self):
        self.shims.add("python3", works=False).add("python", works=False).add("py")
        self.scenario(["python3", "python", "py"])

    def test_no_python_answers_nothing(self):
        # A failed hook blocks every tool on agy, and so does "{}" from PreToolUse. Empty output
        # with exit 0 is "no objection".
        for command in hook_commands():
            with self.subTest(command=command[-60:]):
                proc = run_shell(command, self.shims.env(), json.dumps(PAYLOAD).encode())
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), b"")

    def test_every_hook_mode_answers_json(self):
        self.shims.add("python")
        for command in hook_commands():
            with self.subTest(command=command[-60:]):
                proc = run_shell(command, self.shims.env(), json.dumps(PAYLOAD).encode())
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIsInstance(json.loads(proc.stdout.decode()), dict)


class TestStdinEncoding(unittest.TestCase):
    """PYTHONIOENCODING reproduces the Windows locale code page on any platform."""

    PROMPT = "請修改 src/app.py 的登入流程，並補上單元測試"

    def run_hook(self, encoding, prefix=b""):
        payload = {
            "conversationId": "encoding-test",
            "modelName": "gemini-4-argon-high",
            "toolCall": {"name": "invoke_subagent", "args": {"Subagents": [
                {"TypeName": "flash-worker", "Role": "r", "Model": "inherit", "Prompt": self.PROMPT}]}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            payload["artifactDirectoryPath"] = tmp
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            env["PYTHONIOENCODING"] = encoding
            env["PYTHONUTF8"] = "0"
            env["TMP"] = env["TEMP"] = env["TMPDIR"] = tmp
            proc = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "pre-tool-use"],
                input=prefix + json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                capture_output=True, env=env, timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode("utf-8")

    def test_utf8_prompt_survives_a_legacy_code_page(self):
        for encoding in ("cp950", "cp1252", "utf-8"):
            with self.subTest(encoding=encoding):
                out = self.run_hook(encoding)
                self.assertNotIn("�", out)
                self.assertIsInstance(json.loads(out), dict)

    def test_byte_order_mark_is_ignored(self):
        # With a BOM left in, json.loads fails and the payload is dropped: the hook would see no tool call.
        with_bom = json.loads(self.run_hook("cp950", prefix=b"\xef\xbb\xbf"))
        without = json.loads(self.run_hook("cp950"))
        self.assertEqual(with_bom, without)
        self.assertNotEqual(without, {"decision": "allow"})


if __name__ == "__main__":
    unittest.main()
