#!/usr/bin/env python3
"""Per-session state lock, atomic save and lock release (ported from mitosis v2.3.0, agy parts).
Run: python3 -m unittest discover -s tests"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "enforce-orchestrator.py"
ENV_PREFIXES = ("AGY_", "MITOSIS_")
CID = "6c0f93da-2912-400b-9b41-a46eacc1bfa3"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eo = _load("eo_state_lock", "enforce-orchestrator.py")


def spawn_payload(artifact_dir, i=0):
    return {
        "conversationId": CID,
        "artifactDirectoryPath": artifact_dir,
        "modelName": "Gemini 4 Argon (Medium)",
        "toolCall": {"name": "invoke_subagent", "args": {"Subagents": [{
            "TypeName": "flash-lite-scout", "Model": "flash_lite", "Role": "scout %d" % i,
            "Prompt": "Locate parse_token in src/ (%d)" % i}]}},
    }


class StateLockTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(ENV_PREFIXES):
                os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        eo._release_state_locks()
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(ENV_PREFIXES)}
        env.update(extra)
        return env

    def read_state(self):
        return json.loads((Path(self.tmp.name) / eo.STATE_FILENAME).read_text(encoding="utf-8"))

    def popen(self, mode, **env):
        return subprocess.Popen(
            [sys.executable, "-B", str(SCRIPT), mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, env=self.env(**env),
        )

    @unittest.skipIf(fcntl is None, "needs fcntl")
    def test_parallel_spawns_do_not_lose_pending_entries(self):
        procs = [(self.popen("pre-tool-use"), spawn_payload(self.tmp.name, i)) for i in range(8)]
        for p, payload in procs:  # all started, then fed back to back
            p.stdin.write(json.dumps(payload))
            p.stdin.close()
        for p, _ in procs:
            self.assertEqual(p.wait(timeout=60), 0, p.stderr.read())
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(len(self.read_state()["pending_spawns"]), 8)

    @unittest.skipIf(fcntl is None, "needs fcntl")
    def test_lock_timeout_fails_open(self):
        fd = os.open(str(Path(self.tmp.name) / eo.LOCK_FILENAME), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            start = time.monotonic()
            p = self.popen("pre-tool-use", AGY_ARGON_LOCK_TIMEOUT="0.2")
            out, err = p.communicate(json.dumps(spawn_payload(self.tmp.name)), timeout=30)
            elapsed = time.monotonic() - start
        finally:
            os.close(fd)
        self.assertEqual(p.returncode, 0, err)
        self.assertEqual(json.loads(out)["decision"], "allow")
        self.assertLess(elapsed, 5)
        self.assertEqual(len(self.read_state()["pending_spawns"]), 1)

    @unittest.skipIf(fcntl is None, "needs fcntl")
    def test_locks_released_after_each_handler(self):
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "0.5"
        start = time.monotonic()
        for i in range(2):
            eo.handle_pre_tool_use(spawn_payload(self.tmp.name, i))
            self.assertEqual(eo._HELD_LOCKS, {})
        self.assertLess(time.monotonic() - start, 0.4)
        # Another process can take the lock straight away.
        fd = os.open(str(Path(self.tmp.name) / eo.LOCK_FILENAME), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    @unittest.skipIf(fcntl is None, "needs fcntl")
    def test_nested_acquisition_is_reentrant(self):
        payload = {"conversationId": CID, "artifactDirectoryPath": self.tmp.name}
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "0.5"
        start = time.monotonic()
        eo.load_state(payload)
        eo.load_state(payload)
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertEqual(len(eo._HELD_LOCKS), 1)

    def test_lock_timeout_env_parsing(self):
        self.assertEqual(eo.state._lock_timeout(), 2.0)
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "0.25"
        self.assertEqual(eo.state._lock_timeout(), 0.25)
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "bogus"
        self.assertEqual(eo.state._lock_timeout(), 2.0)

    def test_peek_state_is_lock_free(self):
        payload = {"conversationId": CID, "artifactDirectoryPath": self.tmp.name}
        state = eo.load_state(payload)
        state["consecutive_failures"] = 3
        eo.save_state(payload, state)
        eo._release_state_locks()
        self.assertEqual(eo._peek_state(payload)["consecutive_failures"], 3)
        self.assertEqual(eo._HELD_LOCKS, {})

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        payload = {"conversationId": CID, "artifactDirectoryPath": self.tmp.name}
        for i in range(5):
            state = eo.load_state(payload)
            state["consecutive_failures"] = i
            eo.save_state(payload, state)
        eo._release_state_locks()
        self.assertEqual(self.read_state()["consecutive_failures"], 4)
        leftovers = [p.name for p in Path(self.tmp.name).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
