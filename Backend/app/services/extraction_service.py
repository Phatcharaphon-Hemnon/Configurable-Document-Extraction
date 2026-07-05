from pathlib import Path
import base64
import json
import re
from urllib import error, request
from typing import Any

from Backend.app.agents.extractors import ExtractionContext, ExtractorFactory
from Backend.app.agents.judge import JudgeAgent
from Backend.app.agents.router import RouterAgent
from Backend.app.agents.validator import ValidatorAgent
from Backend.app.core.config import Settings
from Backend.app.schemas.documents import BatchCreateResponse, BatchStatusResponse, DocumentLanguage, DocumentType, EvaluateResponse, ExtractionResult, FileUploadMeta, ExtractionField
from Backend.app.services.job_store import InMemoryJobStore
from Backend.app.services.knowledge_base import KnowledgeBaseRepository

# Load optional extraction patterns
_PATTERNS_PATH = Path(__file__).resolve().parents[1] / "data" / "extraction_patterns.json"
try:
    with open(_PATTERNS_PATH, "r", encoding="utf-8") as _pf:
        _EXTRACTION_PATTERNS = json.load(_pf)
except Exception:
    _EXTRACTION_PATTERNS = {}


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.router = RouterAgent(settings)
        self.extractor_factory = ExtractorFactory()
        self.validator = ValidatorAgent()
        self.judge = JudgeAgent(settings)
        self.job_store = job_store or InMemoryJobStore()
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))

    def list_templates(self) -> list[Any]:
        return self.knowledge_base.list_templates()

    def evaluate(self, prediction: dict[str, Any], ground_truth: dict[str, Any], source_text: str | None = None) -> EvaluateResponse:
        matched_fields = 0
        false_positives = 0
        false_negatives = 0
        mismatches = [
            {"field": field_name, "predicted": prediction.get(field_name), "expected": expected_value}
            for field_name, expected_value in ground_truth.items()
            if prediction.get(field_name) != expected_value
        ]

        for field_name, expected_value in ground_truth.items():
            if prediction.get(field_name) == expected_value:
                matched_fields += 1
            else:
                false_negatives += 1

        for field_name, predicted_value in prediction.items():
            if ground_truth.get(field_name) != predicted_value:
                false_positives += 1

        precision = matched_fields / (matched_fields + false_positives) if (matched_fields + false_positives) else 1.0
        recall = matched_fields / (matched_fields + false_negatives) if (matched_fields + false_negatives) else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        return EvaluateResponse(
            score=f1,
            precision=precision,
            recall=recall,
            f1=f1,
            summary="Evaluation completed",
            mismatches=mismatches,
        )

    def extract(
        self,
        filename: str,
        content_type: str | None,
        text: str,
        raw_content: bytes | None = None,
        ocr_text: str | None = None,
    ) -> ExtractionResult:
        # Prepare full text (prefer explicit OCR text, then provided text, then image extraction)
        final_text = ""
        if ocr_text is not None and ocr_text.strip():
            final_text = ocr_text.strip()
        elif text is not None and text.strip():
            final_text = text
        elif raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type):
            img_text = self._extract_text_from_image(raw_content=raw_content, content_type=content_type)
            final_text = img_text or ""

        # routing/classify using text hint; if there is no OCR/text for an image, classify directly from the image.
        if not final_text.strip() and raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type):
            routing = self._classify_image_document(filename=filename, content_type=content_type, raw_content=raw_content)
        else:
            routing = self.router.classify(filename=filename, content_type=content_type, text_hint=final_text)
        extractor = self.extractor_factory.create(routing.doc_type)
        # include content hash metadata
        metadata = {"filename": filename, "content_type": content_type}
        if raw_content is not None:
            try:
                import hashlib

                metadata["content_hash"] = hashlib.sha256(raw_content).hexdigest()
            except Exception:
                metadata["content_hash"] = None

        context = ExtractionContext(
            text=final_text,
            metadata=metadata,
            image_bytes=raw_content if (raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type)) else None,
            image_mime_type=content_type if (raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type)) else None,
        )
        extracted_fields = extractor.extract(context)

        # Merge key/value pairs found in the text
        def _parse_key_values(src_text: str) -> dict[str, ExtractionField]:
            pairs: dict[str, ExtractionField] = {}
            if not src_text:
                return pairs
            pattern = re.compile(r"^\s*([A-Za-z0-9 _\-()]+?)\s*[:\-\t]\s*(.+?)\s*$")
            for line in src_text.splitlines():
                m = pattern.match(line)
                if m:
                    key = m.group(1).strip().lower().replace(" ", "_")
                    val = m.group(2).strip()
                    if key and val:
                        pairs[key] = ExtractionField(value=val, confidence=0.5, source_span=line.strip())
            return pairs

        try:
            for k, v in _parse_key_values(final_text).items():
                if k not in extracted_fields or extracted_fields[k].value is None:
                    extracted_fields[k] = v
        except Exception:
            pass

        # Apply configured patterns (generic)
        try:
            patterns = _EXTRACTION_PATTERNS.get("generic", {})
            for field, regex_list in patterns.items():
                if field in extracted_fields and extracted_fields[field].value is not None:
                    continue
                for rx in regex_list:
                    try:
                        m = re.search(rx, final_text, re.IGNORECASE)
                    except re.error:
                        m = None
                    if m:
                        val = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                        # normalize amount
                        if field == "total_amount":
                            try:
                                num = re.sub(r"[^0-9.\-]", "", val)
                                parsed = float(num) if num else None
                                extracted_fields[field] = ExtractionField(value=parsed, confidence=0.85, source_span=val)
                            except Exception:
                                extracted_fields[field] = ExtractionField(value=val, confidence=0.6, source_span=val)
                        else:
                            extracted_fields[field] = ExtractionField(value=val, confidence=0.8, source_span=val)
                        break
        except Exception:
            pass

        validation = self.validator.validate(routing.doc_type, extracted_fields)
        judge_result = self.judge.evaluate(
            prediction={field_name: field.value for field_name, field in extracted_fields.items()},
            source_text=final_text,
        )

        return ExtractionResult(
            request=FileUploadMeta(filename=filename, content_type=content_type, size_bytes=len(final_text.encode("utf-8")) if final_text else None),
            doc_type=routing.doc_type,
            language=routing.language,
            routing_reason=routing.reason,
            extracted_fields=extracted_fields,
            full_text=final_text if final_text else None,
            validation=validation,
            judge=judge_result,
        )

    def _is_image_upload(self, filename: str, content_type: str | None) -> bool:
        lower_name = filename.lower()
        if content_type is not None and content_type.startswith("image/"):
            return True
        return lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"))

    def _extract_text_from_image(self, raw_content: bytes, content_type: str | None) -> str | None:
        api_key = self.settings.gemini_api_key.strip()
        model_name = self.settings.recommended_extraction_model_name.strip()

        if not api_key or not model_name.startswith("gemini"):
            return None

        mime_type = content_type or "image/png"
        encoded_image = base64.b64encode(raw_content).decode("utf-8")
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Extract all readable text from this document image. "
                                "Return only the text content, preserve line breaks, and do not add commentary."
                            ),
                        },
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": encoded_image,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        request_object = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(request_object, timeout=20) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return None

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

        extracted_text: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                extracted_text.append(part["text"])

        joined_text = "".join(extracted_text).strip()
        return joined_text or None

    def create_batch(self) -> BatchCreateResponse:
        job = self.job_store.create()
        return BatchCreateResponse(job_id=job.job_id, status=job.status)

    def get_batch_status(self, job_id):
        job = self.job_store.get(job_id)
        if job is None:
            return None
        result = None
        if job.result is not None:
            result = ExtractionResult.model_validate(job.result)
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result)

    def _classify_image_document(self, filename: str, content_type: str | None, raw_content: bytes):
        """Ask Gemini to classify the document directly from image bytes."""
        api_key = self.settings.gemini_api_key.strip()
        model_name = self.settings.recommended_extraction_model_name.strip()

        def _fallback_from_filename() -> object:
            lower_name = filename.lower()
            if any(token in lower_name for token in ["invoice", "bill", "statement", "receipt"]):
                return type(self.router.classify(filename=filename, content_type=content_type, text_hint="invoice"))(
                    doc_type=DocumentType.INVOICE,
                    language=DocumentLanguage.EN,
                    reason="Fallback classification from filename keywords",
                    confidence=0.55,
                )
            if any(token in lower_name for token in ["po", "purchase", "order"]):
                return type(self.router.classify(filename=filename, content_type=content_type, text_hint="po"))(
                    doc_type=DocumentType.PURCHASE_ORDER,
                    language=DocumentLanguage.EN,
                    reason="Fallback classification from filename keywords",
                    confidence=0.55,
                )
            if any(token in lower_name for token in ["delivery", "note", "dn", "grn", "packing"]):
                return type(self.router.classify(filename=filename, content_type=content_type, text_hint="delivery"))(
                    doc_type=DocumentType.DELIVERY_NOTE,
                    language=DocumentLanguage.EN,
                    reason="Fallback classification from filename keywords",
                    confidence=0.55,
                )
            return type(self.router.classify(filename=filename, content_type=content_type, text_hint="invoice"))(
                doc_type=DocumentType.INVOICE,
                language=DocumentLanguage.EN,
                reason="Fallback classification for image without OCR",
                confidence=0.4,
            )

        if not api_key or not model_name.startswith("gemini"):
            return _fallback_from_filename()

        mime_type = content_type or "image/png"
        encoded_image = base64.b64encode(raw_content).decode("utf-8")
        prompt = (
            "Classify this document image into exactly one of these types: invoice, po, delivery_note. "
            "Return JSON only with keys: doc_type, language, reason. "
            "doc_type must be one of invoice, po, delivery_note. language must be en or th. "
            "If the document looks like a billing statement, tax invoice, or account statement, classify as invoice."
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime_type, "data": encoded_image}},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        request_object = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(request_object, timeout=30) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return _fallback_from_filename()

        candidates = response_payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return _fallback_from_filename()

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            payload = json.loads(text)
            doc_type = str(payload.get("doc_type", "")).strip().lower()
            language = str(payload.get("language", "en")).strip().lower()
            reason = str(payload.get("reason", "Classified directly from image")).strip()
        except Exception:
            return _fallback_from_filename()

        if doc_type == DocumentType.INVOICE.value:
            routed_doc_type = DocumentType.INVOICE
        elif doc_type == DocumentType.PURCHASE_ORDER.value:
            routed_doc_type = DocumentType.PURCHASE_ORDER
        elif doc_type == DocumentType.DELIVERY_NOTE.value:
            routed_doc_type = DocumentType.DELIVERY_NOTE
        else:
            return _fallback_from_filename()

        routed_language = DocumentLanguage.TH if language == DocumentLanguage.TH.value else DocumentLanguage.EN
        return type(self.router.classify(filename=filename, content_type=content_type, text_hint=filename))(
            doc_type=routed_doc_type,
            language=routed_language,
            reason=reason,
            confidence=1.0,
        )
