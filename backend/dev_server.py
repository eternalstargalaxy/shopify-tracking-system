from __future__ import annotations

import sys


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError:
        sys.stderr.write(
            "uvicorn is not installed. Install it first, then run: "
            "py -3.12 -m uvicorn backend.app.main:app --reload\n"
        )
        raise SystemExit(1)

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
