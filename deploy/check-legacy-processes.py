#!/usr/bin/env python3
"""Refuse a parallel launch; never print command lines or environment secrets."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
targets = {"web": root / ".venv/bin/opencode-web"}
selected = sys.argv[1:]
if not selected or any(name not in targets for name in selected):
    raise SystemExit("usage: check-legacy-processes.py web")

conflicts = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        cwd = (proc / "cwd").resolve(strict=True)
        args = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")
        # Console-script paths occur in argv[0] or argv[1]. Do not scan
        # arbitrary arguments, which can contain another command as data.
        paths = {(cwd / arg).absolute() for arg in args[:2] if arg}
        for name in selected:
            if targets[name] in paths:
                conflicts.append((name, proc.name))
    except (OSError, ValueError):
        continue

if conflicts:
    for name, pid in conflicts:
        print(f"Refusing duplicate {name} launch: existing PID {pid}", file=sys.stderr)
    raise SystemExit(1)
