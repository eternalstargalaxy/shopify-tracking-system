from __future__ import annotations

import json
import sys

from backend.app.db import init_db
from backend.app.observability import send_daily_usage_report


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else None
    init_db()
    summary = send_daily_usage_report(day)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
