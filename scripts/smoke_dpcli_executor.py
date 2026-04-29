from __future__ import annotations

import json

from skills.dpcli_executor import DPCLIExecutor


def main() -> int:
    executor = DPCLIExecutor(session="autoweb-smoke", headless=True)
    opened = executor.open("about:blank")
    if not opened.get("ok"):
        print(json.dumps(opened, ensure_ascii=False, indent=2))
        return 1

    snapshot = executor.snapshot(mode="agent_summary")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if snapshot.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
