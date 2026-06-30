from app.core.config import Settings
from app.schemas.documents import JudgeIssue, JudgeResult


class JudgeAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, prediction: dict[str, object], source_text: str | None = None) -> JudgeResult:
        issues: list[JudgeIssue] = []

        if source_text is None:
            return JudgeResult(score=0.5, issues=[], notes=f"Judge model: {self.settings.judge_model_name}. Source text not provided.")

        for field_name, value in prediction.items():
            if value is None:
                issues.append(JudgeIssue(field=field_name, message="Predicted field is null"))

        score = max(0.0, 1.0 - (len(issues) * 0.1))
        return JudgeResult(
            score=score,
            issues=issues,
            notes=f"Judge model: {self.settings.judge_model_name}. Compare extraction against source text and flag hallucinations.",
        )
