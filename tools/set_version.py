"""Stamp the release version (and repo) into constelyric.py. Used by the GitHub Actions build."""
import os
import pathlib
import re
import sys

p = pathlib.Path("constelyric.py")
s = p.read_text(encoding="utf-8")
s = re.sub(r'APP_VERSION = "[^"]*"', f'APP_VERSION = "{sys.argv[1]}"', s)
repo = os.environ.get("GITHUB_REPOSITORY")
if repo:
    s = re.sub(r'GITHUB_REPO = "[^"]*"', f'GITHUB_REPO = "{repo}"', s)
p.write_text(s, encoding="utf-8")
print("stamped", sys.argv[1], repo or "")
