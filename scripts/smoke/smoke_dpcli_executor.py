from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.dpcli_executor import DPCLIExecutor


def main() -> int:
    executor = DPCLIExecutor(session="autoweb-smoke", headless=True)
    try:
        opened = executor.open("about:blank")
        if not opened.get("ok"):
            print(json.dumps(opened, ensure_ascii=False, indent=2))
            return 1

        snapshot = executor.snapshot(mode="agent_summary")
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0 if snapshot.get("ok") else 1
    finally:
        executor.session_close()


if __name__ == "__main__":
    raise SystemExit(main())
