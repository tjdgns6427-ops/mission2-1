"""python -m budget_app 실행을 지원한다."""

import sys

from .cli import main


def _use_utf8_console() -> None:
    """Windows 파이프/터미널에서도 한글 입출력을 UTF-8로 통일한다."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    _use_utf8_console()
    raise SystemExit(main())
