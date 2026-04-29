"""Diagnostic: connect to candidate image-to-3D Spaces and print
their current API signatures, so we know how to call them.

Run with:

    .\\.venv\\Scripts\\python.exe probe_spaces.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Load .env if present so HF_TOKEN is picked up
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

from gradio_client import Client  # noqa: E402

CANDIDATE_SPACES = [
    "stabilityai/TripoSR",
    "tencent/Hunyuan3D-2",
    "tencent/Hunyuan3D-2mini",
    "tencent/Hunyuan3D-2.1",
    "JeffreyXiang/TRELLIS",
    "Wuvin/Unique3D",
    "VAST-AI/TripoSG",
]


def probe(space_id: str) -> None:
    print("\n" + "=" * 78)
    print(f"SPACE: {space_id}")
    print("=" * 78)
    token = os.environ.get("HF_TOKEN") or None
    try:
        client = Client(space_id, hf_token=token, verbose=False)
    except Exception as e:
        print(f"  CONNECT FAILED: {type(e).__name__}: {e}")
        return

    print("  Connected. Listing API endpoints:\n")
    try:
        # The 'str' return is human-readable; great for debugging.
        api_text = client.view_api(return_format=None) or ""
        if isinstance(api_text, dict):
            # Newer gradio_client returns a dict
            for name, info in api_text.items():
                print(f"  endpoint: {name}")
                params = info.get("parameters") or info.get("inputs") or []
                returns = info.get("returns") or info.get("outputs") or []
                for p in params:
                    print(f"      param : {p}")
                for r in returns:
                    print(f"      return: {r}")
                print()
        else:
            for line in str(api_text).splitlines():
                print("  " + line)
    except Exception:
        print("  view_api() failed:")
        traceback.print_exc()


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    spaces = [target] if target else CANDIDATE_SPACES
    for s in spaces:
        try:
            probe(s)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
