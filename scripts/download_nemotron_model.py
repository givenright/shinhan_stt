from __future__ import annotations

import argparse
from pathlib import Path

import httpx


DEFAULT_URL = "https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/resolve/main/nemotron-speech-streaming-en-0.6b.nemo"
DEFAULT_OUT = Path("artifacts/models/nvidia/nemotron-speech-streaming-en-0.6b/nemotron-speech-streaming-en-0.6b.nemo")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", args.url, follow_redirects=True, timeout=None, trust_env=False) as response:
        response.raise_for_status()
        with output.open("wb") as file:
            for chunk in response.iter_bytes(1024 * 1024):
                file.write(chunk)
    print(output)


if __name__ == "__main__":
    main()
