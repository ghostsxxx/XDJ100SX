#!/usr/bin/env python3
"""Sets keys in a Mixxx mixxx.cfg, keeping everything else.

Usage: set_cfg.py FILE GROUP KEY VALUE [GROUP KEY VALUE ...]
GROUP without brackets, e.g. XdjSync Enabled 1.
"""
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    args = sys.argv[2:]
    if len(args) % 3:
        sys.exit(__doc__)
    groups = {}  # "[Group]" -> {key: value}, insertion ordered
    current = None
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = groups.setdefault(line, {})
            elif line and current is not None:
                key, _, value = line.partition(" ")
                current[key] = value
    for i in range(0, len(args), 3):
        groups.setdefault(f"[{args[i]}]", {})[args[i + 1]] = args[i + 2]
    out = []
    for group, entries in groups.items():
        out.append(group)
        out += [f"{k} {v}" for k, v in entries.items()]
        out.append("")
    path.write_text("\n".join(out))


if __name__ == "__main__":
    main()
