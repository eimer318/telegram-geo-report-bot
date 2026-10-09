"""Dependency-free repository hygiene check for CI and release review."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".git", ".venv", ".cache", ".local", "__pycache__", "build", "dist"}
FORBIDDEN_NAMES = {
    ".env",
    ".env.test",
    ".env.production",
    "entries.enc",
    "entries.json",
    "entries.geojson",
    "admins.enc",
    "channel_publish_state.enc",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".example", ".gitignore"
}
PATTERNS = {
    "telegram_bot_token": re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{35,}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "assigned_fernet_key": re.compile(
        rb"(?:ENCRYPTION_KEY|FERNET_KEY)\s*=\s*[A-Za-z0-9_-]{43}="
    ),
}


def main() -> int:
    findings = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or any(part in SKIP_PARTS or part.endswith(".egg-info") for part in path.parts)
        ):
            continue
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES:
            findings.append(f"runtime file: {relative}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore"}:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            findings.append(f"unreadable: {relative}")
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{name}: {relative}")
    if findings:
        print("Repository security check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Repository security check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
