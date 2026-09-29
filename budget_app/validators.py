"""CLI와 서비스가 공통으로 사용하는 입력 검증 함수."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Iterable, cast

from .errors import ValidationError
from .models import TransactionType

_CATEGORY_PATTERN = re.compile(r"^[^\s,;|]{1,30}$")


def validate_date(value: str) -> str:
    """YYYY-MM-DD 형식의 실제 존재하는 날짜를 반환한다."""
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError(
            f"날짜 형식이 올바르지 않습니다: {value!r}",
            "YYYY-MM-DD 형식의 실제 날짜를 입력하세요. 예: 2024-01-15",
        ) from exc
    normalized = parsed.strftime("%Y-%m-%d")
    if normalized != value.strip():
        raise ValidationError(
            f"날짜 형식이 올바르지 않습니다: {value!r}",
            "월과 일은 두 자리로 입력하세요. 예: 2024-01-05",
        )
    return normalized


def validate_month(value: str) -> str:
    """YYYY-MM 형식의 월을 반환한다."""
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m")
    except ValueError as exc:
        raise ValidationError(
            f"월 형식이 올바르지 않습니다: {value!r}",
            "YYYY-MM 형식으로 입력하세요. 예: 2024-01",
        ) from exc
    normalized = parsed.strftime("%Y-%m")
    if normalized != value.strip():
        raise ValidationError(
            f"월 형식이 올바르지 않습니다: {value!r}",
            "월은 두 자리로 입력하세요. 예: 2024-01",
        )
    return normalized


def validate_type(value: str) -> TransactionType:
    """income 또는 expense만 허용한다."""
    normalized = value.strip().lower()
    if normalized not in {"income", "expense"}:
        raise ValidationError(
            f"허용되지 않은 거래 타입입니다: {value!r}",
            "수입은 income, 지출은 expense로 입력하세요.",
        )
    return cast(TransactionType, normalized)


def validate_positive_amount(value: str | int) -> int:
    """0보다 큰 정수 금액을 반환한다."""
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"금액은 정수여야 합니다: {value!r}",
            "소수점과 쉼표 없이 양의 정수를 입력하세요. 예: 15000",
        ) from exc
    if amount <= 0:
        raise ValidationError(
            f"금액은 0보다 커야 합니다: {amount}",
            "1 이상의 정수를 입력하세요.",
        )
    return amount


def validate_category_name(value: str) -> str:
    """저장과 CSV 처리가 안전한 카테고리 이름인지 확인한다."""
    normalized = value.strip()
    if not _CATEGORY_PATTERN.fullmatch(normalized):
        raise ValidationError(
            f"카테고리 이름이 올바르지 않습니다: {value!r}",
            "1~30자의 공백 없는 이름을 사용하고 쉼표(,), 세미콜론(;), |는 제외하세요.",
        )
    return normalized


def parse_tags(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """쉼표/세미콜론 문자열 또는 반복 가능한 값을 중복 없는 태그로 만든다."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw_tags = re.split(r"[,;]", value)
    else:
        raw_tags = list(value)
    result: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_tags:
        tag = str(raw_tag).strip()
        if not tag:
            continue
        if len(tag) > 30:
            raise ValidationError(
                f"태그가 너무 깁니다: {tag!r}",
                "각 태그는 30자 이내로 입력하세요.",
            )
        key = tag.casefold()
        if key not in seen:
            result.append(tag)
            seen.add(key)
    return tuple(result)


def validate_positive_limit(value: str | int) -> int:
    """목록 개수 옵션이 양의 정수인지 확인한다."""
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("개수는 정수여야 합니다.", "--limit 또는 --top 뒤에 양의 정수를 입력하세요.") from exc
    if limit <= 0:
        raise ValidationError("개수는 0보다 커야 합니다.", "1 이상의 정수를 입력하세요.")
    return limit

