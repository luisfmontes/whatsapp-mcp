#!/usr/bin/env python3
"""Fail when a tracked file gains a new phone-number-shaped string.

Why a baseline instead of a plain grep: this repo legitimately contains synthetic
numbers in tests and docs (5562999999999 and friends), and a check that fires on
every commit is a check people learn to ignore. So the rule is not "no numbers",
it is "no NEW numbers" -- every occurrence that exists today is recorded in
scripts/personal-data-baseline.txt, and anything not in that list fails the build.

Adding a line to the baseline is the escape hatch, and it is deliberately a
visible diff: someone has to look at the number and decide it is synthetic.

What the guard covers, and why it grew: it started scanning tracked files only, and
that let a real number through in the one place nobody looks -- the commit MESSAGE of
d5fcf48, the very commit that redacted the number from the file. `git ls-files` does
not see commit messages, so the guard could not have caught it. It now also scans the
messages of the commits a branch adds on top of its base.

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


def base_ref():
    """The ref this branch is measured against, or None when it cannot be resolved.

    CI passes it explicitly (the PR base); locally we fall back to origin/main. When
    neither resolves -- a fresh clone, a detached checkout -- the commit-message scan
    is skipped rather than failing: a guard that errors out on setup noise is a guard
    people route around.
    """
    import os
    for ref in (os.environ.get("PERSONAL_DATA_BASE"), "origin/main", "main"):
        if not ref:
            continue
        ok = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True, text=True,
        )
        if ok.returncode == 0:
            return ref
    return None


def scan_commit_messages():
    """Identifiers in the messages of commits this branch adds on top of its base.

    Only the new commits: history already published is not something this check can
    fix, and failing on it forever would just teach people to ignore the guard.
    """
    ref = base_ref()
    if ref is None:
        return {}, None
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--format=%B", f"{ref}..HEAD"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return {}, None
    found = {}
    for kind, pattern in PATTERNS:
        for match in pattern.findall(out.stdout):
            found.setdefault(f"{kind}:{match}", set()).add(f"mensagem de commit ({ref}..HEAD)")
    return found, ref


def main():
    found = scan()
    msg_found, msg_ref = scan_commit_messages()
    for key, where in msg_found.items():
        found.setdefault(key, set()).update(where)
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

    if msg_ref is None:
        print("Personal-data guard: base ref not resolvable, commit messages not scanned.")

    stale = sorted(allowed - set(found))
    if stale:
        print("Personal-data guard: baseline entries no longer present (safe to delete):")
        for key in stale:
            print(f"  {key}")

    print(f"Personal-data guard: ok ({len(found)} known identifiers, all in the baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
