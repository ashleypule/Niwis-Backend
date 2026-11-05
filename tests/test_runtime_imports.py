"""The image installs requirements.txt only, so the app must import without the development packages."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]

BLOCK = """
import sys
blocked = set(sys.argv[1:])
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in blocked:
            raise ModuleNotFoundError(f'{name} is a development package', name=name)
sys.meta_path.insert(0, Block())
import app.main
"""


def _names(file: str) -> set[str]:
    lines = (ROOT / file).read_text(encoding="utf-8").splitlines()
    pins = (re.match(r"[A-Za-z0-9._-]+", line) for line in lines if not line.startswith(("#", "-")))
    return {m.group().lower().replace("-", "_") for m in pins if m}


def test_the_app_imports_without_the_development_packages() -> None:
    dev_only = _names("requirements-dev.txt") - _names("requirements.txt")
    assert "httpx" in dev_only
    done = subprocess.run(
        [sys.executable, "-c", BLOCK, *sorted(dev_only)], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stderr[-1500:]
