"""입력 검증과 저장소를 연결하는 용돈 기입장 비즈니스 로직."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from .errors import NotFoundError, ValidationError
from .models import CategoryExpense, ImportResult, MonthlySummary, SearchCriteria, Transaction
from .storage import BudgetStore, CategoryStore, TransactionRepository, atomic_text_writer
from .validators import (
    parse_tags,
    validate_category_name,
    validate_date,
    validate_month,
    validate_positive_amount,
    validate_type,
)

CSV_COLUMNS = ("date", "type", "category", "amount", "memo", "tags")


class BudgetService:
    """거래·카테고리·예산 저장소를 조합해 기능을 제공한다."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.transactions = TransactionRepository(data_dir / "transactions.jsonl")
        self.categories = CategoryStore(data_dir / "categories.jsonl")
        self.budgets = BudgetStore(data_dir / "budgets.jsonl")

    def _require_category(self, category: str) -> str:
        normalized = validate_category_name(category)
        if not self.categories.exists(normalized):
            available = ", ".join(self.categories.iter_all())
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: {normalized!r}",
                f"category add로 추가하거나 다음 중 하나를 사용하세요: {available}",
            )
        return normalized

    def add_transaction(
        self,
        *,
        date: str,
        transaction_type: str,
        category: str,
        amount: str | int,
        memo: str = "",
        tags: str | tuple[str, ...] | None = None,
    ) -> Transaction:
        transaction = Transaction(
            id=self.transactions.make_id(self.transactions.next_sequence()),
            type=validate_type(transaction_type),
            date=validate_date(date),
            amount=validate_positive_amount(amount),
            category=self._require_category(category),
            memo=memo.strip(),
            tags=parse_tags(tags),
        )
        self.transactions.append(transaction)
        return transaction

    def iter_latest(self, limit: int | None = None) -> Iterator[Transaction]:
        count = 0
        for transaction in self.transactions.iter_latest():
            if limit is not None and count >= limit:
                break
            yield transaction
            count += 1

    def search(self, criteria: SearchCriteria) -> Iterator[Transaction]:
        for transaction in self.transactions.iter_latest():
            if criteria.matches(transaction):
                yield transaction

    def update_transaction(self, transaction_id: str, changes: dict[str, Any]) -> Transaction:
        current = self.transactions.get(transaction_id)
        if not changes:
            raise ValidationError("수정할 항목이 없습니다.", "날짜, 타입, 카테고리, 금액, 메모, 태그 중 하나를 지정하세요.")
        validated: dict[str, Any] = {}
        if "date" in changes:
            validated["date"] = validate_date(str(changes["date"]))
        if "type" in changes:
            validated["type"] = validate_type(str(changes["type"]))
        if "category" in changes:
            validated["category"] = self._require_category(str(changes["category"]))
        if "amount" in changes:
            validated["amount"] = validate_positive_amount(changes["amount"])
        if "memo" in changes:
            validated["memo"] = str(changes["memo"]).strip()
        if "tags" in changes:
            validated["tags"] = parse_tags(changes["tags"])
        updated = replace(current, **validated)
        self.transactions.update(updated)
        return updated

    def delete_transaction(self, transaction_id: str) -> None:
        self.transactions.delete(transaction_id)

    def summarize(self, month: str, top: int) -> MonthlySummary:
        normalized_month = validate_month(month)
        total_income = 0
        total_expense = 0
        transaction_count = 0
        expenses: dict[str, int] = {}
        for transaction in self.transactions.iter_all():
            if not transaction.date.startswith(normalized_month + "-"):
                continue
            transaction_count += 1
            if transaction.type == "income":
                total_income += transaction.amount
            else:
                total_expense += transaction.amount
                expenses[transaction.category] = expenses.get(transaction.category, 0) + transaction.amount
        ranked = sorted(expenses.items(), key=lambda item: (-item[1], item[0]))[:top]
        return MonthlySummary(
            month=normalized_month,
            total_income=total_income,
            total_expense=total_expense,
            top_expenses=tuple(CategoryExpense(category, amount) for category, amount in ranked),
            transaction_count=transaction_count,
            budget=self.budgets.get(normalized_month),
        )

    def set_budget(self, month: str, amount: str | int) -> tuple[str, int]:
        normalized_month = validate_month(month)
        normalized_amount = validate_positive_amount(amount)
        self.budgets.set(normalized_month, normalized_amount)
        return normalized_month, normalized_amount

    def get_budget(self, month: str) -> int:
        normalized_month = validate_month(month)
        amount = self.budgets.get(normalized_month)
        if amount is None:
            raise NotFoundError(
                f"{normalized_month}에 설정된 예산이 없습니다.",
                f"budget set --month {normalized_month} --amount 금액 명령으로 설정하세요.",
            )
        return amount

    def add_category(self, category: str) -> str:
        normalized = validate_category_name(category)
        self.categories.add(normalized)
        return normalized

    def remove_category(self, category: str, replacement: str | None = None) -> int:
        normalized = validate_category_name(category)
        if not self.categories.exists(normalized):
            raise NotFoundError(
                f"카테고리 {normalized!r}이(가) 존재하지 않습니다.",
                "category list로 정확한 이름을 확인하세요.",
            )
        changed = 0
        if self.transactions.category_is_used(normalized):
            if replacement is None:
                raise ValidationError(
                    f"카테고리 {normalized!r}을(를) 사용하는 거래가 있습니다.",
                    "--replacement 뒤에 대체할 기존 카테고리를 지정하세요.",
                )
            normalized_replacement = self._require_category(replacement)
            if normalized_replacement == normalized:
                raise ValidationError("삭제 대상과 대체 카테고리가 같습니다.", "서로 다른 카테고리를 지정하세요.")
            changed = self.transactions.replace_category(normalized, normalized_replacement)
        self.categories.remove(normalized)
        return changed

    def import_csv(self, input_path: Path) -> ImportResult:
        if not input_path.is_file():
            raise NotFoundError(f"가져올 CSV 파일이 없습니다: {input_path}", "--from 뒤의 파일 경로를 확인하세요.")
        imported = 0
        skipped = 0
        issues: list[str] = []
        sequence = self.transactions.next_sequence()
        with input_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None or any(column not in reader.fieldnames for column in CSV_COLUMNS):
                raise ValidationError(
                    "CSV 헤더가 올바르지 않습니다.",
                    "date,type,category,amount,memo,tags 열을 포함한 UTF-8 CSV를 사용하세요.",
                )
            for line_number, row in enumerate(reader, start=2):
                try:
                    transaction = Transaction(
                        id=self.transactions.make_id(sequence),
                        type=validate_type(row.get("type") or ""),
                        date=validate_date(row.get("date") or ""),
                        amount=validate_positive_amount(row.get("amount") or ""),
                        category=self._require_category(row.get("category") or ""),
                        memo=(row.get("memo") or "").strip(),
                        tags=parse_tags(row.get("tags") or ""),
                    )
                    self.transactions.append(transaction)
                    sequence += 1
                    imported += 1
                except ValidationError as exc:
                    skipped += 1
                    if len(issues) < 20:
                        issues.append(f"{line_number}행: {exc.message}")
        return ImportResult(imported=imported, skipped=skipped, issues=tuple(issues))

    def export_csv(self, output_path: Path, criteria: SearchCriteria) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with atomic_text_writer(output_path) as file:
            writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for transaction in self.search(criteria):
                writer.writerow(
                    {
                        "date": transaction.date,
                        "type": transaction.type,
                        "category": transaction.category,
                        "amount": transaction.amount,
                        "memo": transaction.memo,
                        "tags": ";".join(transaction.tags),
                    }
                )
                count += 1
        return count
