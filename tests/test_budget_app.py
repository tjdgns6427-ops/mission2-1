from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from budget_app.cli import main
from budget_app.errors import NotFoundError, ValidationError
from budget_app.models import SearchCriteria
from budget_app.services import BudgetService
from budget_app.storage import iter_jsonl_reverse
from budget_app.validators import (
    parse_tags,
    validate_date,
    validate_month,
    validate_positive_amount,
    validate_type,
)


class ValidatorTests(unittest.TestCase):
    def test_valid_date(self) -> None:
        self.assertEqual(validate_date("2024-02-29"), "2024-02-29")

    def test_impossible_date_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_date("2024-02-30")

    def test_non_padded_date_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_date("2024-2-3")

    def test_valid_month(self) -> None:
        self.assertEqual(validate_month("2024-12"), "2024-12")

    def test_invalid_month_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_month("2024-13")

    def test_transaction_types(self) -> None:
        self.assertEqual(validate_type("INCOME"), "income")
        self.assertEqual(validate_type("expense"), "expense")
        with self.assertRaises(ValidationError):
            validate_type("saving")

    def test_amount_must_be_positive_integer(self) -> None:
        self.assertEqual(validate_positive_amount("15000"), 15000)
        for invalid in ("1.5", "abc", "0", "-1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validate_positive_amount(invalid)

    def test_tags_are_trimmed_and_deduplicated(self) -> None:
        self.assertEqual(parse_tags("meal, friend;MEAL"), ("meal", "friend"))


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_directory.name)
        self.service = BudgetService(self.data_dir)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def add_sample_transactions(self) -> None:
        self.service.add_transaction(
            date="2024-01-10", transaction_type="income", category="salary", amount=300000, memo="월급", tags="work"
        )
        self.service.add_transaction(
            date="2024-01-11", transaction_type="expense", category="food", amount=15000, memo="친구와 점심", tags="meal,friend"
        )
        self.service.add_transaction(
            date="2024-01-12", transaction_type="expense", category="transport", amount=20000, memo="교통카드", tags="commute"
        )

    def test_initialization_creates_three_jsonl_files(self) -> None:
        self.assertTrue((self.data_dir / "transactions.jsonl").is_file())
        self.assertTrue((self.data_dir / "categories.jsonl").is_file())
        self.assertTrue((self.data_dir / "budgets.jsonl").is_file())

    def test_default_categories_are_created(self) -> None:
        categories = list(self.service.categories.iter_all())
        self.assertIn("food", categories)
        self.assertIn("salary", categories)

    def test_add_generates_sequential_unique_id(self) -> None:
        self.add_sample_transactions()
        ids = [transaction.id for transaction in self.service.transactions.iter_all()]
        self.assertEqual(ids, ["TX-000001", "TX-000002", "TX-000003"])

    def test_add_rejects_unknown_category(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add_transaction(
                date="2024-01-01", transaction_type="expense", category="unknown", amount=1000
            )

    def test_latest_iterator_streams_reverse_date_order(self) -> None:
        self.add_sample_transactions()
        ids = [transaction.id for transaction in self.service.iter_latest(limit=2)]
        self.assertEqual(ids, ["TX-000003", "TX-000002"])

    def test_out_of_order_add_is_still_listed_by_latest_date(self) -> None:
        newer = self.service.add_transaction(
            date="2024-12-31", transaction_type="expense", category="food", amount=2000
        )
        older = self.service.add_transaction(
            date="2024-01-01", transaction_type="expense", category="food", amount=1000
        )
        self.assertEqual([item.id for item in self.service.iter_latest()], [newer.id, older.id])

    def test_date_update_reorders_transaction(self) -> None:
        self.add_sample_transactions()
        self.service.update_transaction("TX-000001", {"date": "2024-12-31"})
        self.assertEqual(next(self.service.iter_latest()).id, "TX-000001")

    def test_search_combines_conditions(self) -> None:
        self.add_sample_transactions()
        criteria = SearchCriteria(
            from_date="2024-01-01",
            to_date="2024-01-31",
            category="food",
            transaction_type="expense",
            query="점심",
            tag="friend",
        )
        results = list(self.service.search(criteria))
        self.assertEqual([item.id for item in results], ["TX-000002"])

    def test_summary_calculates_income_expense_and_top(self) -> None:
        self.add_sample_transactions()
        summary = self.service.summarize("2024-01", top=1)
        self.assertEqual(summary.total_income, 300000)
        self.assertEqual(summary.total_expense, 35000)
        self.assertEqual(summary.balance, 265000)
        self.assertEqual(summary.top_expenses[0].category, "transport")

    def test_summary_reports_no_data(self) -> None:
        summary = self.service.summarize("2025-01", top=3)
        self.assertEqual(summary.transaction_count, 0)

    def test_budget_is_persistent_and_summary_uses_it(self) -> None:
        self.add_sample_transactions()
        self.service.set_budget("2024-01", 30000)
        new_service = BudgetService(self.data_dir)
        summary = new_service.summarize("2024-01", top=3)
        self.assertEqual(summary.budget, 30000)
        self.assertTrue(summary.is_over_budget)
        self.assertAlmostEqual(summary.budget_usage_percent or 0, 116.666, places=2)

    def test_get_missing_budget_raises(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.get_budget("2024-01")

    def test_update_changes_only_selected_fields(self) -> None:
        self.add_sample_transactions()
        updated = self.service.update_transaction("TX-000002", {"amount": 17000, "memo": "점심 변경"})
        self.assertEqual(updated.amount, 17000)
        self.assertEqual(updated.memo, "점심 변경")
        self.assertEqual(updated.category, "food")

    def test_update_requires_at_least_one_change(self) -> None:
        self.add_sample_transactions()
        with self.assertRaises(ValidationError):
            self.service.update_transaction("TX-000001", {})

    def test_delete_and_missing_id(self) -> None:
        self.add_sample_transactions()
        self.service.delete_transaction("TX-000002")
        self.assertEqual(len(list(self.service.transactions.iter_all())), 2)
        with self.assertRaises(NotFoundError):
            self.service.delete_transaction("TX-999999")

    def test_category_add_and_remove(self) -> None:
        self.service.add_category("gift")
        self.assertTrue(self.service.categories.exists("gift"))
        changed = self.service.remove_category("gift")
        self.assertEqual(changed, 0)
        self.assertFalse(self.service.categories.exists("gift"))

    def test_duplicate_category_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add_category("food")

    def test_used_category_requires_replacement(self) -> None:
        self.add_sample_transactions()
        with self.assertRaises(ValidationError):
            self.service.remove_category("food")

    def test_used_category_can_be_replaced_then_removed(self) -> None:
        self.add_sample_transactions()
        changed = self.service.remove_category("food", "other")
        self.assertEqual(changed, 1)
        self.assertFalse(self.service.categories.exists("food"))
        self.assertEqual(self.service.transactions.get("TX-000002").category, "other")

    def test_export_and_import_csv(self) -> None:
        self.add_sample_transactions()
        export_path = self.data_dir / "export.csv"
        count = self.service.export_csv(
            export_path,
            SearchCriteria(from_date="2024-01-01", to_date="2024-01-31"),
        )
        self.assertEqual(count, 3)
        second_dir = self.data_dir / "second"
        second_service = BudgetService(second_dir)
        result = second_service.import_csv(export_path)
        self.assertEqual((result.imported, result.skipped), (3, 0))

    def test_import_skips_invalid_rows(self) -> None:
        csv_path = self.data_dir / "mixed.csv"
        csv_path.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-01-01,expense,food,1000,정상,meal\n"
            "2024-99-01,expense,food,-1,오류,bad\n",
            encoding="utf-8",
        )
        result = self.service.import_csv(csv_path)
        self.assertEqual((result.imported, result.skipped), (1, 1))
        self.assertEqual(len(result.issues), 1)

    def test_import_skips_row_with_missing_required_cells(self) -> None:
        csv_path = self.data_dir / "short-row.csv"
        csv_path.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-01-01,expense\n",
            encoding="utf-8",
        )
        result = self.service.import_csv(csv_path)
        self.assertEqual((result.imported, result.skipped), (0, 1))

    def test_import_rejects_wrong_header(self) -> None:
        csv_path = self.data_dir / "wrong.csv"
        csv_path.write_text("hello,world\n1,2\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.service.import_csv(csv_path)

    def test_reverse_jsonl_generator_handles_many_lines(self) -> None:
        path = self.data_dir / "many.jsonl"
        path.write_text("".join(f'{{"number":{number}}}\n' for number in range(2000)), encoding="utf-8")
        numbers = [item["number"] for item in iter_jsonl_reverse(path)]
        self.assertEqual(numbers, list(reversed(range(2000))))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_directory.name)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_cli_add_dialog_and_list(self) -> None:
        inputs = ["2024-01-15", "expense", "food", "15000", "점심", "meal"]
        with patch("builtins.input", side_effect=inputs), redirect_stdout(io.StringIO()) as output:
            exit_code = main(["add", "--data-dir", str(self.data_dir)])
        self.assertEqual(exit_code, 0)
        self.assertIn("TX-000001", output.getvalue())

        with redirect_stdout(io.StringIO()) as list_output:
            exit_code = main(["--data-dir", str(self.data_dir), "list", "--limit", "1"])
        self.assertEqual(exit_code, 0)
        self.assertIn("점심", list_output.getvalue())

    def test_cli_error_has_nonzero_exit_and_no_traceback(self) -> None:
        with redirect_stderr(io.StringIO()) as error_output:
            exit_code = main(["--data-dir", str(self.data_dir), "delete", "--id", "TX-999999"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("[오류]", error_output.getvalue())
        self.assertNotIn("Traceback", error_output.getvalue())

    def test_export_requires_condition(self) -> None:
        with redirect_stderr(io.StringIO()) as error_output:
            exit_code = main(["--data-dir", str(self.data_dir), "export", "--out", str(self.data_dir / "x.csv")])
        self.assertEqual(exit_code, 2)
        self.assertIn("--month", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
