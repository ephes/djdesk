"""Audit only the frozen production lock; propagate every failure to CI."""

import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="djdesk-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(requirements),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--no-deps",
                "--disable-pip",
                "--requirement",
                str(requirements),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
