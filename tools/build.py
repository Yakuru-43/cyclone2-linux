#!/usr/bin/env python3
"""Build dist/cyclone2: the whole tool as one executable file (a Python zipapp).

Standard library only. The result needs nothing but Python 3.9+.
"""

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(out=ROOT / "dist" / "cyclone2"):
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        shutil.copytree(ROOT / "cyclone2", stage / "cyclone2",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (stage / "__main__.py").write_text(
            "import sys\nfrom cyclone2.cli import main\nsys.exit(main())\n")
        zipapp.create_archive(stage, out, interpreter="/usr/bin/env python3", compressed=True)
    return out


if __name__ == "__main__":
    path = build()
    print(f"built {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    sys.exit(0)
