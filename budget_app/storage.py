"""JSONL 파일 입출력과 원자적 수정/삭제를 담당한다."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, TextIO

from .errors import DataCorruptionError, NotFoundError, ValidationError
from .models import Transaction

DEFAULT_CATEGORIES = ("food", "transport", "rent", "salary", "leisure", "health", "education", "other")


def _json_line(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """JSONL 파일을 한 줄씩 읽어 딕셔너리를 생성한다."""
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise TypeError("JSON 객체가 아닙니다")
            except (json.JSONDecodeError, TypeError) as exc:
                raise DataCorruptionError(
                    f"{path.name}의 {line_number}번째 줄이 손상되었습니다.",
                    "해당 줄을 올바른 JSON 객체로 고치거나 백업 파일로 복구하세요.",
                ) from exc
            yield data


def _iter_lines_reverse(path: Path, block_size: int = 8192) -> Iterator[str]:
    """큰 파일도 통째로 읽지 않고 마지막 줄부터 거꾸로 생성한다."""
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        position = file.tell()
        remainder = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            file.seek(position)
            block = file.read(read_size) + remainder
            parts = block.split(b"\n")
            remainder = parts[0]
            for raw_line in reversed(parts[1:]):
                if raw_line.strip():
                    yield raw_line.decode("utf-8")
        if remainder.strip():
            yield remainder.decode("utf-8")


def iter_jsonl_reverse(path: Path) -> Iterator[dict[str, Any]]:
    """JSONL 파일을 마지막 레코드부터 스트리밍한다."""
    for reverse_index, line in enumerate(_iter_lines_reverse(path), start=1):
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise TypeError("JSON 객체가 아닙니다")
        except (json.JSONDecodeError, TypeError) as exc:
            raise DataCorruptionError(
                f"{path.name}의 뒤에서 {reverse_index}번째 데이터가 손상되었습니다.",
                "손상된 JSONL 줄을 수정하거나 백업 파일로 복구하세요.",
            ) from exc
        yield data


@contextmanager
def atomic_text_writer(path: Path) -> Iterator[TextIO]:
    """같은 폴더의 임시 파일에 쓴 뒤 os.replace로 안전하게 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            yield temporary_file
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


class TransactionRepository:
    """transactions.jsonl에 거래를 저장하고 스트리밍 조회한다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def iter_all(self) -> Iterator[Transaction]:
        for data in iter_jsonl(self.path):
            try:
                yield Transaction.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise DataCorruptionError(
                    f"{self.path.name}에 필수 필드가 없거나 값의 타입이 잘못되었습니다.",
                    "id, type, date, amount, category 필드를 확인하세요.",
                ) from exc

    def iter_latest(self) -> Iterator[Transaction]:
        for data in iter_jsonl_reverse(self.path):
            try:
                yield Transaction.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise DataCorruptionError(
                    f"{self.path.name}에 필수 필드가 없거나 값의 타입이 잘못되었습니다.",
                    "id, type, date, amount, category 필드를 확인하세요.",
                ) from exc

    def next_sequence(self) -> int:
        maximum = 0
        for transaction in self.iter_all():
            if transaction.id.startswith("TX-") and transaction.id[3:].isdigit():
                maximum = max(maximum, int(transaction.id[3:]))
        return maximum + 1

    @staticmethod
    def make_id(sequence: int) -> str:
        return f"TX-{sequence:06d}"

    def append(self, transaction: Transaction) -> None:
        """날짜·ID 오름차순을 유지해 역방향 조회가 최신순이 되게 저장한다."""
        inserted = False
        new_key = (transaction.date, transaction.id)
        with atomic_text_writer(self.path) as output:
            for existing in self.iter_all():
                if not inserted and new_key < (existing.date, existing.id):
                    output.write(_json_line(transaction.to_dict()))
                    inserted = True
                output.write(_json_line(existing.to_dict()))
            if not inserted:
                output.write(_json_line(transaction.to_dict()))

    def get(self, transaction_id: str) -> Transaction:
        for transaction in self.iter_all():
            if transaction.id == transaction_id:
                return transaction
        raise NotFoundError(
            f"거래 ID {transaction_id!r}에 해당하는 데이터가 없습니다.",
            "list 명령으로 존재하는 거래 ID를 확인하세요.",
        )

    def update(self, replacement: Transaction) -> None:
        found = False
        inserted = False
        replacement_key = (replacement.date, replacement.id)
        with atomic_text_writer(self.path) as output:
            for transaction in self.iter_all():
                if transaction.id == replacement.id:
                    found = True
                    continue
                if not inserted and replacement_key < (transaction.date, transaction.id):
                    output.write(_json_line(replacement.to_dict()))
                    inserted = True
                output.write(_json_line(transaction.to_dict()))
            if not found:
                raise NotFoundError(
                    f"거래 ID {replacement.id!r}에 해당하는 데이터가 없습니다.",
                    "list 명령으로 존재하는 거래 ID를 확인하세요.",
                )
            if not inserted:
                output.write(_json_line(replacement.to_dict()))

    def delete(self, transaction_id: str) -> None:
        found = False
        with atomic_text_writer(self.path) as output:
            for transaction in self.iter_all():
                if transaction.id == transaction_id:
                    found = True
                    continue
                output.write(_json_line(transaction.to_dict()))
            if not found:
                raise NotFoundError(
                    f"거래 ID {transaction_id!r}에 해당하는 데이터가 없습니다.",
                    "list 명령으로 존재하는 거래 ID를 확인하세요.",
                )

    def category_is_used(self, category: str) -> bool:
        return any(transaction.category == category for transaction in self.iter_all())

    def replace_category(self, old: str, new: str) -> int:
        changed = 0
        with atomic_text_writer(self.path) as output:
            for transaction in self.iter_all():
                if transaction.category == old:
                    transaction = Transaction(
                        id=transaction.id,
                        type=transaction.type,
                        date=transaction.date,
                        amount=transaction.amount,
                        category=new,
                        memo=transaction.memo,
                        tags=transaction.tags,
                    )
                    changed += 1
                output.write(_json_line(transaction.to_dict()))
        return changed


class CategoryStore:
    """categories.jsonl의 카테고리 목록을 관리한다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._write_defaults()

    def _write_defaults(self) -> None:
        with atomic_text_writer(self.path) as output:
            for category in DEFAULT_CATEGORIES:
                output.write(_json_line({"name": category}))

    def iter_all(self) -> Iterator[str]:
        for data in iter_jsonl(self.path):
            if "name" not in data:
                raise DataCorruptionError(
                    f"{self.path.name}에 name 필드가 없는 데이터가 있습니다.",
                    "각 줄을 {\"name\": \"카테고리\"} 형태로 수정하세요.",
                )
            yield str(data["name"])

    def exists(self, category: str) -> bool:
        return any(item == category for item in self.iter_all())

    def count(self) -> int:
        return sum(1 for _ in self.iter_all())

    def add(self, category: str) -> None:
        if self.exists(category):
            raise ValidationError(
                f"카테고리 {category!r}은(는) 이미 존재합니다.",
                "category list로 현재 목록을 확인하세요.",
            )
        with self.path.open("a", encoding="utf-8", newline="") as file:
            file.write(_json_line({"name": category}))
            file.flush()
            os.fsync(file.fileno())

    def remove(self, category: str) -> None:
        if self.count() <= 1:
            raise ValidationError("마지막 카테고리는 삭제할 수 없습니다.", "먼저 다른 카테고리를 추가하세요.")
        found = False
        with atomic_text_writer(self.path) as output:
            for item in self.iter_all():
                if item == category:
                    found = True
                    continue
                output.write(_json_line({"name": item}))
            if not found:
                raise NotFoundError(
                    f"카테고리 {category!r}이(가) 존재하지 않습니다.",
                    "category list로 정확한 이름을 확인하세요.",
                )


class BudgetStore:
    """budgets.jsonl의 월별 예산을 관리한다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def iter_all(self) -> Iterator[tuple[str, int]]:
        for data in iter_jsonl(self.path):
            try:
                yield str(data["month"]), int(data["amount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataCorruptionError(
                    f"{self.path.name}의 month 또는 amount 값이 잘못되었습니다.",
                    "각 줄을 {\"month\": \"YYYY-MM\", \"amount\": 양의정수} 형태로 수정하세요.",
                ) from exc

    def get(self, month: str) -> int | None:
        for stored_month, amount in self.iter_all():
            if stored_month == month:
                return amount
        return None

    def set(self, month: str, amount: int) -> None:
        replaced = False
        with atomic_text_writer(self.path) as output:
            for stored_month, stored_amount in self.iter_all():
                if stored_month == month:
                    output.write(_json_line({"month": month, "amount": amount}))
                    replaced = True
                else:
                    output.write(_json_line({"month": stored_month, "amount": stored_amount}))
            if not replaced:
                output.write(_json_line({"month": month, "amount": amount}))
