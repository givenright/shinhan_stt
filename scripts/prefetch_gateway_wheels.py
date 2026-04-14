from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    out_dir = Path("artifacts/wheels/gateway")
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "-r", "apps/gateway/requirements.txt", "-d", str(out_dir)],
        check=True,
    )


if __name__ == "__main__":
    main()
