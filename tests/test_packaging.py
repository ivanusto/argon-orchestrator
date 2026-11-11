#!/usr/bin/env python3
"""Release hygiene: manifest, pyproject and changelog versions agree, hook
commands point at existing scripts with known modes, and agent files are
pinned to flash or flash_lite.  Run: python -m unittest discover -s tests"""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN_MODES = {"session-start", "pre-invocation", "pre-tool-use", "post-tool-use", "stop"}


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestVersionsAgree(unittest.TestCase):
    def test_single_version(self):
        found = {
            "plugin.json": json.loads(read("plugin.json"))["version"],
            "pyproject.toml": tomllib.loads(read("pyproject.toml"))["project"]["version"],
            "CHANGELOG.md": re.search(r"^## (\d+\.\d+\.\d+)", read("CHANGELOG.md"), re.M).group(1),
        }
        self.assertEqual(len(set(found.values())), 1, found)

    def test_names_agree(self):
        self.assertEqual(json.loads(read("plugin.json"))["name"], "just-agy-orchestrator")
        self.assertEqual(tomllib.loads(read("pyproject.toml"))["project"]["name"], "just-agy-orchestrator")


class TestHooks(unittest.TestCase):
    def commands(self):
        return re.findall(r'"command":\s*"([^"]+)"', read("hooks.json"))

    def test_hooks_json_is_valid(self):
        json.loads(read("hooks.json"))
        self.assertTrue(self.commands())

    def test_commands_point_at_existing_scripts(self):
        for cmd in self.commands():
            with self.subTest(command=cmd):
                match = re.search(r"(scripts/[\w.-]+\.py)", cmd)
                self.assertIsNotNone(match, cmd)
                self.assertTrue((ROOT / match.group(1)).is_file(), cmd)

    def test_modes_are_known(self):
        for cmd in self.commands():
            with self.subTest(command=cmd):
                self.assertIn(cmd.split(" || ")[0].split()[-1], KNOWN_MODES)

    def test_every_command_carries_the_full_launcher_chain(self):
        # agy runs hooks via `cmd /c` on Windows, where python3 can be a Store alias (exit 9009).
        for cmd in self.commands():
            with self.subTest(command=cmd):
                mode = cmd.split(" || ")[0].split()[-1]
                script = "./scripts/enforce-orchestrator.py " + mode
                self.assertEqual(
                    cmd, " || ".join(["python3 " + script, "python " + script, "py -3 " + script, "exit 0"]))
                self.assertNotIn("echo", cmd)  # `{}` from PreToolUse is a denial on agy


class TestAgents(unittest.TestCase):
    def test_agent_frontmatter(self):
        dirs = sorted(p for p in (ROOT / "agents").iterdir() if p.is_dir())
        self.assertTrue(dirs)
        for d in dirs:
            with self.subTest(agent=d.name):
                text = (d / "agent.md").read_text(encoding="utf-8")
                front = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
                self.assertEqual(re.search(r"^name:\s*(\S+)", front, re.M).group(1), d.name)
                self.assertIn(re.search(r"^model:\s*(\S+)", front, re.M).group(1), {"flash", "flash_lite"})


if __name__ == "__main__":
    unittest.main()
