"""용돈 기입장의 데이터 모양을 정의한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterator, Literal

TransactionType = Literal["income", "expense"]


@dataclass(frozen=True)
class Transaction:
    """수입 또는 지출 한 건."""

    id: str
    type: TransactionType
    date: str
    amount: int
    category: str
    memo: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON으로 저장 가능한 딕셔너리로 변환한다."""
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transaction":
        """저장 파일에서 읽은 딕셔너리를 Transaction으로 변환한다."""
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),  # type: ignore[arg-type]
            date=str(data["date"]),
            amount=int(data["amount"]),
            category=str(data["category"]),
            memo=str(data.get("memo", "")),
            tags=tuple(str(tag) for tag in data.get("tags", [])),
        )


@dataclass(frozen=True)
class SearchCriteria:
    """거래 검색에 사용할 선택 조건."""

    from_date: str | None = None
    to_date: str | None = None
    category: str | None = None
    transaction_type: TransactionType | None = None
    query: str | None = None
    tag: str | None = None

    def matches(self, transaction: Transaction) -> bool:
        """거래가 모든 검색 조건을 만족하는지 확인한다."""
        if self.from_date and transaction.date < self.from_date:
            return False
        if self.to_date and transaction.date > self.to_date:
            return False
        if self.category and transaction.category != self.category:
            return False
        if self.transaction_type and transaction.type != self.transaction_type:
            return False
        if self.query and self.query.casefold() not in transaction.memo.casefold():
            return False
        if self.tag and self.tag.casefold() not in (tag.casefold() for tag in transaction.tags):
            return False
        return True


@dataclass(frozen=True)
class CategoryExpense:
    """카테고리별 지출 합계."""

    category: str
    amount: int


@dataclass(frozen=True)
class MonthlySummary:
    """한 달의 수입·지출·예산 요약."""

    month: str
    total_income: int
    total_expense: int
    top_expenses: tuple[CategoryExpense, ...]
    transaction_count: int
    budget: int | None = None

    @property
    def balance(self) -> int:
        return self.total_income - self.total_expense

    @property
    def budget_usage_percent(self) -> float | None:
        if self.budget is None:
            return None
        if self.budget == 0:
            return 0.0
        return self.total_expense / self.budget * 100

    @property
    def is_over_budget(self) -> bool:
        return self.budget is not None and self.total_expense > self.budget


@dataclass(frozen=True)
class ImportResult:
    """CSV 가져오기 처리 결과."""

    imported: int
    skipped: int
    issues: tuple[str, ...] = ()


TransactionIterator = Iterator[Transaction]

