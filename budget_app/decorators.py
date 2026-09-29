"""여러 명령에 공통으로 적용하는 데코레이터."""

from __future__ import annotations

from functools import wraps
import json
import sys
from typing import Callable, ParamSpec, TypeVar

from .errors import BudgetAppError

P = ParamSpec("P")
R = TypeVar("R", bound=int)


def friendly_errors(function: Callable[P, R]) -> Callable[P, R | int]:
    """예외를 스택트레이스 대신 원인과 해결 힌트로 바꿔 출력한다."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | int:
        try:
            return function(*args, **kwargs)
        except BudgetAppError as exc:
            print(f"[오류] {exc.message}", file=sys.stderr)
            print(f"[힌트] {exc.hint}", file=sys.stderr)
            return 2
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[오류] 파일을 처리하지 못했습니다: {exc}", file=sys.stderr)
            print("[힌트] 파일 경로, 권한, JSONL/CSV 형식을 확인하세요.", file=sys.stderr)
            return 3
        except (EOFError, KeyboardInterrupt):
            print("\n[오류] 입력이 중단되었습니다.", file=sys.stderr)
            print("[힌트] 명령을 다시 실행해 입력을 완료하세요.", file=sys.stderr)
            return 130
        except Exception as exc:  # CLI에서는 내부 스택트레이스를 노출하지 않는다.
            print(f"[오류] 예상하지 못한 문제가 발생했습니다: {exc}", file=sys.stderr)
            print("[힌트] 입력과 저장 파일을 확인한 뒤 다시 시도하세요.", file=sys.stderr)
            return 1

    return wrapper

