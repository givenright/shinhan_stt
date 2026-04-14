from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    out_dir = Path("artifacts/wheels/nemotron")
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "-r", "services/nemotron_asr/requirements.txt", "-d", str(out_dir)],
        check=True,
    )


if __name__ == "__main__":
    main()
