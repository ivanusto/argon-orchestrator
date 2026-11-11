#!/usr/bin/env python3
"""JUST_AGY_* environment names: new name works, legacy AGY_ARGON_* still works, new name wins.
Run: python3 -m unittest discover -s tests"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from just_agy_hook import common  # noqa: E402

PREFIXES = ("JUST_AGY_", "AGY_ARGON_", "AGY_ALLOW_NON_ARGON_PRIMARY", "MITOSIS_JUDGE_")


class EnvNames(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(PREFIXES):
                os.environ.pop(k)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_new_name_works(self):
        os.environ["JUST_AGY_AUTO_ALLOW"] = "1"
        self.assertTrue(common._env_flag("AUTO_ALLOW"))
        os.environ["JUST_AGY_LOCK_TIMEOUT"] = "7"
        self.assertEqual(common._env("LOCK_TIMEOUT", default="2"), "7")

    def test_legacy_name_still_works(self):
        os.environ["AGY_ARGON_AUTO_ALLOW"] = "1"
        self.assertTrue(common._env_flag("AUTO_ALLOW"))
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "5"
        self.assertEqual(common._env("LOCK_TIMEOUT", default="2"), "5")

    def test_new_name_wins_when_both_set(self):
        os.environ["AGY_ARGON_AUTO_ALLOW"] = "1"
        os.environ["JUST_AGY_AUTO_ALLOW"] = "0"
        self.assertFalse(common._env_flag("AUTO_ALLOW"))
        os.environ["AGY_ARGON_LOCK_TIMEOUT"] = "5"
        os.environ["JUST_AGY_LOCK_TIMEOUT"] = "9"
        self.assertEqual(common._env("LOCK_TIMEOUT"), "9")

    def test_default_when_unset(self):
        self.assertFalse(common._env_flag("AUTO_ALLOW"))
        self.assertEqual(common._env("LOCK_TIMEOUT", default="2"), "2")

    def test_allow_non_primary_all_names(self):
        for name in ("JUST_AGY_ALLOW_NON_PRIMARY", "AGY_ARGON_ALLOW_NON_PRIMARY", "AGY_ALLOW_NON_ARGON_PRIMARY"):
            with self.subTest(name=name):
                os.environ.pop(name, None)
                os.environ[name] = "1"
                self.assertTrue(common._env_flag("ALLOW_NON_PRIMARY"))
                os.environ.pop(name)
        os.environ["AGY_ALLOW_NON_ARGON_PRIMARY"] = "1"
        os.environ["JUST_AGY_ALLOW_NON_PRIMARY"] = "0"
        self.assertFalse(common._env_flag("ALLOW_NON_PRIMARY"))

    def test_judge_chain(self):
        from just_agy_hook import judge
        os.environ["MITOSIS_JUDGE_URL"] = "m"
        self.assertEqual(judge._env("URL"), "m")
        os.environ["AGY_ARGON_JUDGE_URL"] = "a"
        self.assertEqual(judge._env("URL"), "a")
        os.environ["JUST_AGY_JUDGE_URL"] = "j"
        self.assertEqual(judge._env("URL"), "j")

    def test_is_argon_tier_alias(self):
        from just_agy_hook import models
        self.assertIs(models.is_argon_tier, models.is_frontier_tier)


if __name__ == "__main__":
    unittest.main()
