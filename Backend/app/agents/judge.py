from __future__ import annotations

import json
from urllib import error, request

from Backend.app.core.config import Settings
from Backend.app.schemas.documents import JudgeIssue, JudgeResult


class JudgeAgent:
    _gemini_api_base = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, prediction: dict[str, object], source_text: str | None = None) -> JudgeResult:
        if source_text is None:
            return JudgeResult(score=0.5, issues=[], notes=f"Judge model: {self.settings.judge_model_name}. Source text not provided.")

        gemini_result = self._evaluate_with_gemini(prediction=prediction, source_text=source_text)
        if gemini_result is not None:
            return gemini_result

        issues: list[JudgeIssue] = []

        for field_name, value in prediction.items():
            if value is None:
                issues.append(JudgeIssue(field=field_name, message="Predicted field is null"))

        score = max(0.0, 1.0 - (len(issues) * 0.1))
        return JudgeResult(
            score=score,
            issues=issues,
            notes=f"Judge model: {self.settings.judge_model_name}. Compare extraction against source text and flag hallucinations.",
        )

    def _evaluate_with_gemini(self, prediction: dict[str, object], source_text: str) -> JudgeResult | None:
        api_key = self.settings.gemini_api_key.strip()
        model_name = self.settings.judge_model_name.strip()

        if not api_key or not model_name.startswith("gemini"):
            return None

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": self._build_prompt(prediction=prediction, source_text=source_text),
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        url = f"{self._gemini_api_base}/models/{model_name}:generateContent?key={api_key}"
        request_object = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(request_object, timeout=15) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return None

        response_text = self._extract_response_text(response_payload)
        if not response_text:
            return None

        parsed = self._parse_json_response(response_text)
        if parsed is None:
            return None

        return self._build_judge_result(parsed)

    def _build_prompt(self, prediction: dict[str, object], source_text: str) -> str:
        return (
            "You are a strict document-extraction judge. Compare the predicted fields against the source text, "
            "penalize hallucinations, and return JSON only.\n\n"
            "Return an object with these keys:\n"
            "- score: number from 0 to 1\n"
            "- issues: array of objects with field, message, and optional severity\n"
            "- notes: short summary string\n\n"
            f"Predicted fields: {json.dumps(prediction, ensure_ascii=False)}\n\n"
            f"Source text: {source_text}"
        )

    def _extract_response_text(self, response_payload: dict[str, object]) -> str | None:
        candidates = response_payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None

        first_candidate = candidates[0]
        if not isinstance(first_candidate, dict):
            return None

        content = first_candidate.get("content")
        if not isinstance(content, dict):
            return None

        parts = content.get("parts")
        if not isinstance(parts, list):
            return None

        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

        return "".join(text_parts).strip() or None

    def _parse_json_response(self, response_text: str) -> dict[str, object] | None:
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text.strip("`")
            if cleaned_text.startswith("json"):
                cleaned_text = cleaned_text[4:].strip()

        start_index = cleaned_text.find("{")
        end_index = cleaned_text.rfind("}")
        if start_index == -1 or end_index == -1 or end_index <= start_index:
            return None

        try:
            parsed = json.loads(cleaned_text[start_index : end_index + 1])
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None
        return parsed

    def _build_judge_result(self, parsed: dict[str, object]) -> JudgeResult | None:
        score = parsed.get("score")
        notes = parsed.get("notes")
        issues = parsed.get("issues", [])

        if not isinstance(score, (int, float)) or not isinstance(notes, str):
            return None

        judge_issues: list[JudgeIssue] = []
        if isinstance(issues, list):
            for item in issues:
                if not isinstance(item, dict):
                    continue
                field = item.get("field")
                message = item.get("message")
                severity = item.get("severity", "warning")
                if isinstance(field, str) and isinstance(message, str):
                    judge_issues.append(JudgeIssue(field=field, message=message, severity=severity if isinstance(severity, str) else "warning"))

        return JudgeResult(score=max(0.0, min(1.0, float(score))), issues=judge_issues, notes=notes)
