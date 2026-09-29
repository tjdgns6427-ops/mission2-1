"""애플리케이션에서 사용하는 사용자 친화적 예외."""


class BudgetAppError(Exception):
    """사용자에게 원인과 해결 방법을 안내할 수 있는 기본 예외."""

    def __init__(self, message: str, hint: str = "입력값을 확인한 뒤 다시 시도하세요.") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ValidationError(BudgetAppError):
    """입력값이 프로그램의 규칙에 맞지 않을 때 발생한다."""


class NotFoundError(BudgetAppError):
    """요청한 데이터가 존재하지 않을 때 발생한다."""


class DataCorruptionError(BudgetAppError):
    """저장 파일의 JSONL 데이터가 손상되었을 때 발생한다."""

