#!/usr/bin/env python3
"""Fail when a tracked file gains a new phone-number-shaped string.

Why a baseline instead of a plain grep: this repo legitimately contains synthetic
numbers in tests and docs (5562999999999 and friends), and a check that fires on
every commit is a check people learn to ignore. So the rule is not "no numbers",
it is "no NEW numbers" -- every occurrence that exists today is recorded in
scripts/personal-data-baseline.txt, and anything not in that list fails the build.

Adding a line to the baseline is the escape hatch, and it is deliberately a
visible diff: someone has to look at the number and decide it is synthetic.

Background: a real WhatsApp number reached this public repository on 2026-08-08,
pasted into a versioned progress log as smoke-test evidence, and stayed there for
16 days. Rewriting history did not fully remove it -- in a fork network the object
is still served by SHA. The cheap moment to catch it was before the commit.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "scripts" / "personal-data-baseline.txt"

# Brazilian mobile/landline in the shape WhatsApp uses (country code + DDD + number),
# with or without a JID suffix. Deliberately narrow: broad patterns produce noise,
# and noise is how a guard stops being read.
PATTERNS = [
    ("telefone", re.compile(r"\b55\d{10,11}\b")),
    ("jid", re.compile(r"\b\d{10,15}(?::\d+)?@s\.whatsapp\.net\b")),
]

# Binary-ish and vendored paths carry no hand-written evidence.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".exe", ".db", ".sum", ".lock"}
SKIP_PARTS = {"go.sum", "uv.lock"}


def tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        path = Path(line)
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_PARTS:
            continue
        yield line, REPO / path


def load_baseline():
    if not BASELINE.exists():
        return set()
    allowed = set()
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            allowed.add(line)
    return allowed


def scan():
    found = {}
    for rel, full in tracked_files():
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind, pattern in PATTERNS:
            for match in pattern.findall(text):
                found.setdefault(f"{kind}:{match}", set()).add(rel)
    return found


def main():
    found = scan()
    allowed = load_baseline()
    new = {k: v for k, v in found.items() if k not in allowed}

    if new:
        print("Personal-data guard: found identifiers not in the baseline.\n")
        for key in sorted(new):
            print(f"  {key}")
            for rel in sorted(new[key]):
                print(f"      {rel}")
        print(
            "\nIf these are synthetic test values, add each line above to\n"
            f"  {BASELINE.relative_to(REPO)}\n"
            "in the same commit -- the diff is the point: someone has to look at the\n"
            "number and say it is not a real person's."
        )
        return 1

    stale = sorted(allowed - set(found))
    if stale:
        print("Personal-data guard: baseline entries no longer present (safe to delete):")
        for key in stale:
            print(f"  {key}")

    print(f"Personal-data guard: ok ({len(found)} known identifiers, all in the baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
