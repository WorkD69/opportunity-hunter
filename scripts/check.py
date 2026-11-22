#!/usr/bin/env python3
"""Single project check: unittest suite and a gitleaks scan of everything git would commit.

Usage: python scripts/check.py   (python3 on macOS/Linux)
Exit code 0 only if every check is green. A missing tool is red, never a silent skip.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = "PASS", "FAIL"

Result = tuple[str, str]


def run(cmd: list[str], cwd: Path = ROOT) -> int:
    where = "" if cwd == ROOT else f"  (in {cwd.relative_to(ROOT)})"
    print(f"\n$ {' '.join(cmd)}{where}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=False).returncode


def check_unittest() -> Result:
    code = run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    return (PASS, "unittest") if code == 0 else (FAIL, f"unittest failed (exit {code})")


def git_files() -> tuple[list[str] | None, str]:
    """Files git would commit (tracked + untracked not ignored), or None with the reason."""
    if not shutil.which("git"):
        return None, "git not found in PATH"
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, check=False)
    if top.returncode != 0:
        return None, "not a git repo"
    if Path(top.stdout.decode("utf-8", "surrogateescape").strip()).resolve() != ROOT:
        return None, "this folder is not the root of its git repo (nested in another repo?)"
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p], ""


def check_secrets(files: list[str] | None, reason: str) -> Result:
    gitleaks = shutil.which("gitleaks")
    if not gitleaks:
        return FAIL, "gitleaks is not installed (https://github.com/gitleaks/gitleaks#installing)"
    # Run from the scanned folder with "." so finding paths (and .gitleaksignore fingerprints) are repo-relative.
    base = [gitleaks, "dir", ".", "--redact", "--no-banner", "-v", "--gitleaks-ignore-path", str(ROOT)]
    if files is None:
        print(f"\n({reason}: scanning the whole folder, ignored files such as .env included)")
        code = run(base, ROOT)
    elif not files:
        return FAIL, "git sees no files to commit: nothing to scan (is everything ignored?)"
    else:
        # Scan a snapshot of committable files only, so a local .env or node_modules never turns the check red.
        (ROOT / "tmp").mkdir(exist_ok=True)
        snap = Path(tempfile.mkdtemp(prefix="gitleaks-", dir=ROOT / "tmp"))
        try:
            copied = 0
            for rel in files:
                src = ROOT / rel
                if src.is_file():  # deleted-but-tracked files and submodule links have nothing to scan
                    dst = snap / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1
            if not copied:
                return FAIL, "no committable file could be copied for the scan"
            code = run(base, snap)
        finally:
            shutil.rmtree(snap, ignore_errors=True)
    return (PASS, "gitleaks") if code == 0 else (FAIL, "gitleaks found secrets (or crashed)")


def main() -> int:
    files, reason = git_files()
    results: list[Result] = [check_unittest(), check_secrets(files, reason)]

    print("\n=== check ===")
    for status, msg in results:
        print(f"{status:4}  {msg}")
    failed = sum(s == FAIL for s, _ in results)
    print("RESULT: RED" if failed else "RESULT: GREEN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
