"""명령행 인터페이스(CLI) 진입점."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Callable, Sequence, TypeVar

from .decorators import friendly_errors, measure_execution_time
from .errors import ValidationError
from .models import MonthlySummary, SearchCriteria, Transaction
from .services import BudgetService
from .validators import (
    parse_tags,
    validate_date,
    validate_month,
    validate_positive_amount,
    validate_positive_limit,
    validate_type,
)

T = TypeVar("T")


def _add_data_dir_help(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        default="./data",
        metavar="PATH",
        help="JSONL 저장 폴더 (기본값: ./data, 명령 앞/뒤 어디든 지정 가능)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m budget_app",
        description="JSONL 파일 기반 개인 용돈 기입장",
    )
    _add_data_dir_help(parser)
    subparsers = parser.add_subparsers(dest="command", required=True, title="명령")

    subparsers.add_parser("add", help="대화형으로 수입/지출 거래 추가")

    list_parser = subparsers.add_parser("list", help="최신 등록순 거래 목록")
    list_parser.add_argument("--limit", type=validate_positive_limit, default=20, help="출력할 최대 개수 (기본값: 20)")

    search_parser = subparsers.add_parser("search", help="조건에 맞는 거래 검색")
    search_parser.add_argument("--from", dest="from_date", type=validate_date, help="시작 날짜 YYYY-MM-DD")
    search_parser.add_argument("--to", dest="to_date", type=validate_date, help="종료 날짜 YYYY-MM-DD")
    search_parser.add_argument("--category", help="카테고리")
    search_parser.add_argument("--type", dest="transaction_type", type=validate_type, help="income 또는 expense")
    search_parser.add_argument("--q", dest="query", help="메모에 포함된 키워드")
    search_parser.add_argument("--tag", help="포함할 태그")

    summary_parser = subparsers.add_parser("summary", help="월별 수입/지출/예산 요약")
    summary_parser.add_argument("--month", required=True, type=validate_month, help="대상 월 YYYY-MM")
    summary_parser.add_argument("--top", type=validate_positive_limit, default=5, help="카테고리별 지출 상위 개수")

    budget_parser = subparsers.add_parser("budget", help="월 예산 설정/조회")
    budget_commands = budget_parser.add_subparsers(dest="budget_command", required=True)
    budget_set = budget_commands.add_parser("set", help="월 예산 설정")
    budget_set.add_argument("--month", required=True, type=validate_month)
    budget_set.add_argument("--amount", required=True, type=validate_positive_amount)
    budget_show = budget_commands.add_parser("show", help="월 예산 조회")
    budget_show.add_argument("--month", required=True, type=validate_month)

    category_parser = subparsers.add_parser("category", help="카테고리 추가/목록/삭제")
    category_commands = category_parser.add_subparsers(dest="category_command", required=True)
    category_add = category_commands.add_parser("add", help="카테고리 추가")
    category_add.add_argument("name", nargs="?", help="생략하면 대화형으로 입력")
    category_commands.add_parser("list", help="카테고리 목록")
    category_remove = category_commands.add_parser("remove", help="카테고리 삭제")
    category_remove.add_argument("name", nargs="?", help="생략하면 대화형으로 입력")
    category_remove.add_argument("--replacement", help="사용 중인 카테고리를 옮길 기존 카테고리")

    update_parser = subparsers.add_parser("update", help="거래 ID를 기준으로 선택 필드 수정")
    update_parser.add_argument("--id", required=True, dest="transaction_id")
    update_parser.add_argument("--date", type=validate_date)
    update_parser.add_argument("--type", type=validate_type)
    update_parser.add_argument("--category")
    update_parser.add_argument("--amount", type=validate_positive_amount)
    update_parser.add_argument("--memo")
    update_parser.add_argument("--tags", help="쉼표 또는 세미콜론으로 구분, 빈 문자열이면 모두 제거")

    delete_parser = subparsers.add_parser("delete", help="거래 ID를 기준으로 삭제")
    delete_parser.add_argument("--id", required=True, dest="transaction_id")

    import_parser = subparsers.add_parser("import", help="CSV 거래 일괄 가져오기")
    import_parser.add_argument("--from", dest="input_path", required=True, type=Path)

    export_parser = subparsers.add_parser("export", help="조건에 맞는 거래를 CSV로 내보내기")
    export_parser.add_argument("--out", dest="output_path", required=True, type=Path)
    export_parser.add_argument("--month", type=validate_month, help="대상 월 YYYY-MM")
    export_parser.add_argument("--from", dest="from_date", type=validate_date)
    export_parser.add_argument("--to", dest="to_date", type=validate_date)
    return parser


def _extract_data_dir(arguments: Sequence[str]) -> tuple[Path, list[str]]:
    """--data-dir을 명령 앞/뒤 어느 위치에서든 받을 수 있게 분리한다."""
    cleaned: list[str] = []
    selected = "./data"
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--data-dir":
            if index + 1 >= len(arguments):
                raise ValidationError("--data-dir 뒤에 경로가 없습니다.", "예: --data-dir ./my-data")
            selected = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--data-dir="):
            selected = argument.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(argument)
        index += 1
    return Path(selected), cleaned


def _prompt(label: str, validator: Callable[[str], T]) -> T:
    """올바른 값이 들어올 때까지 이유와 힌트를 보여 주며 다시 묻는다."""
    while True:
        raw_value = input(label).strip()
        try:
            return validator(raw_value)
        except ValidationError as exc:
            print(f"[오류] {exc.message}")
            print(f"[힌트] {exc.hint}")


def _format_transaction(transaction: Transaction) -> str:
    tags = ",".join(transaction.tags)
    return (
        f"{transaction.id} | {transaction.date} | {transaction.type} | "
        f"{transaction.category} | {transaction.amount}원 | {transaction.memo} | {tags}"
    )


def _print_transactions(transactions: Any) -> int:
    count = 0
    for transaction in transactions:
        print(_format_transaction(transaction))
        count += 1
    if count == 0:
        print("데이터 없음")
    return count


def _handle_add(service: BudgetService) -> None:
    print("거래 정보를 입력하세요.")
    date = _prompt("날짜(YYYY-MM-DD): ", validate_date)
    transaction_type = _prompt("타입(income/expense): ", validate_type)

    def registered_category(value: str) -> str:
        if not service.categories.exists(value):
            available = ", ".join(service.categories.iter_all())
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: {value!r}",
                f"다음 중 하나를 입력하거나 category add로 추가하세요: {available}",
            )
        return value

    category = _prompt("카테고리: ", registered_category)
    amount = _prompt("금액(양수): ", validate_positive_amount)
    memo = input("메모(선택): ").strip()
    tags = parse_tags(input("태그(쉼표로 구분, 없으면 엔터): "))
    transaction = service.add_transaction(
        date=date,
        transaction_type=transaction_type,
        category=category,
        amount=amount,
        memo=memo,
        tags=tags,
    )
    print(f"[저장 완료] id={transaction.id}")


def _print_summary(summary: MonthlySummary) -> None:
    if summary.transaction_count == 0:
        print(f"{summary.month}: 데이터 없음")
        if summary.budget is not None:
            print(f"예산: {summary.budget}원 (사용률 0.0%)")
        return
    print(f"[{summary.month} 월별 요약]")
    print(f"총 수입: {summary.total_income}원")
    print(f"총 지출: {summary.total_expense}원")
    print(f"잔액: {summary.balance}원")
    if summary.budget is not None:
        print(f"예산: {summary.budget}원 (사용률 {summary.budget_usage_percent:.1f}%)")
        if summary.is_over_budget:
            print(f"[경고] 예산을 {summary.total_expense - summary.budget}원 초과했습니다!")
    print("\n지출 TOP")
    if not summary.top_expenses:
        print("- 지출 데이터 없음")
    for index, expense in enumerate(summary.top_expenses, start=1):
        print(f"{index}) {expense.category} {expense.amount}원")


def _changes_from_args(args: argparse.Namespace) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for argument, field_name in (
        ("date", "date"),
        ("type", "type"),
        ("category", "category"),
        ("amount", "amount"),
        ("memo", "memo"),
        ("tags", "tags"),
    ):
        value = getattr(args, argument)
        if value is not None:
            changes[field_name] = value
    return changes


def _export_criteria(args: argparse.Namespace) -> SearchCriteria:
    if args.month:
        if args.from_date or args.to_date:
            raise ValidationError("--month와 --from/--to를 동시에 사용할 수 없습니다.", "월 또는 날짜 범위 중 하나만 선택하세요.")
        year, month = (int(part) for part in args.month.split("-"))
        if month == 12:
            next_month = f"{year + 1:04d}-01-01"
        else:
            next_month = f"{year:04d}-{month + 1:02d}-01"
        # 문자열 날짜 비교에서 다음 달 1일 직전까지 포함시키기 위한 월 마지막 날짜
        from datetime import datetime, timedelta

        last_day = (datetime.strptime(next_month, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return SearchCriteria(from_date=f"{args.month}-01", to_date=last_day)
    if not args.from_date or not args.to_date:
        raise ValidationError(
            "export에는 --month 또는 --from과 --to가 필요합니다.",
            "예: export --out result.csv --month 2024-01",
        )
    if args.from_date > args.to_date:
        raise ValidationError("시작 날짜가 종료 날짜보다 늦습니다.", "--from은 --to보다 같거나 이른 날짜여야 합니다.")
    return SearchCriteria(from_date=args.from_date, to_date=args.to_date)


@friendly_errors
@measure_execution_time
def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    data_dir, arguments = _extract_data_dir(raw_arguments)
    parser = build_parser()
    args = parser.parse_args(arguments)
    service = BudgetService(data_dir)

    if args.command == "add":
        _handle_add(service)
    elif args.command == "list":
        _print_transactions(service.iter_latest(args.limit))
    elif args.command == "search":
        if args.from_date and args.to_date and args.from_date > args.to_date:
            raise ValidationError("시작 날짜가 종료 날짜보다 늦습니다.", "--from은 --to보다 같거나 이른 날짜여야 합니다.")
        criteria = SearchCriteria(
            from_date=args.from_date,
            to_date=args.to_date,
            category=args.category,
            transaction_type=args.transaction_type,
            query=args.query,
            tag=args.tag,
        )
        _print_transactions(service.search(criteria))
    elif args.command == "summary":
        _print_summary(service.summarize(args.month, args.top))
    elif args.command == "budget":
        if args.budget_command == "set":
            month, amount = service.set_budget(args.month, args.amount)
            print(f"[저장 완료] {month} 예산 {amount}원")
        else:
            amount = service.get_budget(args.month)
            print(f"{args.month} 예산: {amount}원")
    elif args.command == "category":
        if args.category_command == "add":
            name = args.name or input("카테고리명: ").strip()
            category = service.add_category(name)
            print(f"[저장 완료] category={category}")
        elif args.category_command == "list":
            for category in service.categories.iter_all():
                print(f"- {category}")
        else:
            name = args.name or input("삭제할 카테고리명: ").strip()
            changed = service.remove_category(name, args.replacement)
            print(f"[삭제 완료] category={name}, 변경된 거래={changed}건")
    elif args.command == "update":
        updated = service.update_transaction(args.transaction_id, _changes_from_args(args))
        print(f"[수정 완료] {_format_transaction(updated)}")
    elif args.command == "delete":
        service.delete_transaction(args.transaction_id)
        print(f"[삭제 완료] id={args.transaction_id}")
    elif args.command == "import":
        result = service.import_csv(args.input_path)
        print(f"[완료] imported={result.imported}, skipped={result.skipped}")
        for issue in result.issues:
            print(f"[건너뜀] {issue}", file=sys.stderr)
    elif args.command == "export":
        count = service.export_csv(args.output_path, _export_criteria(args))
        print(f"[완료] {args.output_path} ({count} records)")
    return 0
