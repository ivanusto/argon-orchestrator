#!/usr/bin/env python3
"""Routing parity check against a mitosis-orchestrator checkout.

Usage: python3 scripts/routing-parity.py <mitosis_repo>

Runs decide_subagent from mitosis (scripts/mitosis_hook) and from this plugin
(scripts/argon_hook) in two SEPARATE subprocesses over tests/routing_cases.jsonl and
tests/routing_holdout.jsonl plus v2..v8, then prints the counts and every case whose
tier, rule or type_override differ. Exit 1 on any difference or load failure.

The sealed holdout v9 is never read. The judge is forced off in both children.
Python 3.8+, standard library only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARGON_ROOT = HERE.parent
DATASETS = ["routing_cases", "routing_holdout"] + ["routing_holdout_v%d" % n for n in range(2, 9)]

CHILD = r"""
import json, sys
scripts_dir, package = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)
import importlib
routing = importlib.import_module(package + ".routing")
out = []
for spec in json.load(sys.stdin):
    d = routing.decide_subagent(spec)
    out.append([d.tier, d.rule, d.type_override])
json.dump(out, sys.stdout)
"""


def load(path):
    with open(str(path), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run_child(scripts_dir, package, specs):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGY_", "CLAUDE_", "CODEX_", "MITOSIS_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-c", CHILD, str(scripts_dir), package],
        input=json.dumps(specs), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, env=env, cwd=str(scripts_dir),
    )
    if proc.returncode != 0:
        sys.stderr.write("%s child failed:\n%s\n" % (package, proc.stderr))
        sys.exit(1)
    return json.loads(proc.stdout)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write(__doc__)
        return 2
    mitosis_scripts = Path(argv[1]).resolve() / "scripts"
    if not (mitosis_scripts / "mitosis_hook" / "routing.py").is_file():
        sys.stderr.write("not a mitosis checkout: %s\n" % argv[1])
        return 2

    cases = []
    for name in DATASETS:
        path = ARGON_ROOT / "tests" / (name + ".jsonl")
        if not path.is_file():
            sys.stderr.write("missing dataset %s\n" % path)
            return 2
        for case in load(path):
            cases.append((name, case))

    specs = []
    for _, case in cases:
        spec = {"prompt": case["prompt"]}
        if case.get("subagent_type"):
            spec["subagent_type"] = case["subagent_type"]
        specs.append(spec)

    mitosis = run_child(mitosis_scripts, "mitosis_hook", specs)
    argon = run_child(HERE, "argon_hook", specs)

    per_set = {}
    diffs = []
    for (name, case), m, a in zip(cases, mitosis, argon):
        total, bad = per_set.get(name, (0, 0))
        differs = m != a
        per_set[name] = (total + 1, bad + (1 if differs else 0))
        if differs:
            diffs.append((name, case["id"], m, a))

    for name in DATASETS:
        total, bad = per_set.get(name, (0, 0))
        print("%-20s cases=%-4d differences=%d" % (name, total, bad))
    print("total cases=%d differences=%d" % (len(cases), len(diffs)))
    for name, cid, m, a in diffs:
        print("DIFF %s %s: mitosis tier=%s rule=%s type=%s | argon tier=%s rule=%s type=%s"
              % ((name, cid) + tuple(m) + tuple(a)))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
