# AI Guardrails Implementation Plan

> **Document Version:** 1.0  
> **Last Updated:** 2026-08-25  
> **Status:** Implementation Ready

## Overview

This document details the AI guardrails implementation for the Configurable Document Extraction system. Guardrails are security layers that protect against prompt injection, data leakage, hallucination, and misuse of the AI-powered document extraction pipeline.

---

## Table of Contents

1. [Current Security Architecture](#current-security-architecture)
2. [Guardrail Layers](#guardrail-layers)
3. [Implementation Plan](#implementation-plan)
4. [Code Examples](#code-examples)
5. [Testing Strategy](#testing-strategy)
6. [Configuration](#configuration)

---

## Current Security Architecture

### Existing Security Measures

Your project already implements foundational security in `api/app/core/security.py`:

| Component | Status | Location |
|-----------|--------|----------|
| Prompt injection detection | ✅ Implemented | `security.py:12-23` |
| Document text sanitization | ✅ Implemented | `security.py:32-49` |
| Evidence/hallucination validation | ✅ Implemented | `security.py:59-82` |
| Suspicious text detection | ✅ Implemented | `security.py:52-56` |

### Current Injection Patterns

```python
_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?|rules?)",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?|rules?)",
    r"you\s+are\s+now\s+(a|an|the)\s+",
    r"act\s+as\s+(a|an|the)\s+",
    r"new\s+(instructions?|role|persona)\s*:",
    r"system\s*(prompt|message)\s*:",
    r"</?(system|assistant|user|instructions?)>",
    r"(reveal|show|print|repeat)\s+(your|the)\s+(system\s+)?(prompt|instructions)",
    r"extract\s+and\s+(email|send|post)\s+",
    r"api[_\s-]?key|secret[_\s-]?key|password\s*[:=]",
)
```

### Pipeline Security Flow

```
Upload → [Input Guard] → OCR/Vision → [Sanitize] → Router → [Validate] → Extractor → [Check Evidence] → Validator → Judge → [Output Guard] → Response
```

---

## Guardrail Layers

### Layer 1: Input Guards (Pre-Processing)

**Purpose:** Validate and sanitize all inputs before they enter the pipeline.

| Guard | Description | Priority |
|-------|-------------|----------|
| File type validation | MIME type verification, not just extension | High |
| File size limits | Per-type and total size constraints | High |
| Image dimensions | Width/height constraints for vision processing | Medium |
| Rate limiting | Per-user/IP request throttling | High |
| Content size limits | Per-field and per-document character caps | Medium |

### Layer 2: Processing Guards (In-Flight)

**Purpose:** Monitor and control the extraction pipeline execution.

| Guard | Description | Priority |
|-------|-------------|----------|
| Stage timeouts | Per-agent execution time limits | High |
| Token budget | Maximum tokens per request | Medium |
| Circuit breaker | Stop repeated failures | Medium |
| Prompt isolation | Separate system/user content | Low |
| Audit logging | Security event tracking | High |

### Layer 3: Output Guards (Post-Processing)

**Purpose:** Validate and filter all outputs before returning to the client.

| Guard | Description | Priority |
|-------|-------------|----------|
| Schema validation | Verify LLM output matches Pydantic models | High |
| PII detection | Detect sensitive data in extraction results | High |
| Content filtering | Remove inappropriate/harmful content | Medium |
| Error sanitization | Remove internal paths/API keys from errors | High |
| Output length limits | Cap response sizes | Low |

---

## Implementation Plan

### Phase 1: Input Guards (Week 1-2)

#### 1.1 File Upload Validation

**File:** `api/app/guards/input_guard.py`

```python
"""Input validation guards for file uploads and requests."""

from __future__ import annotations

import magic
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

# Allowed MIME types for document processing
ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
    # Documents
    "application/pdf",
}

# File size limits (in bytes)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50MB

# Image dimension limits
MAX_IMAGE_WIDTH = 10000
MAX_IMAGE_HEIGHT = 10000


class InputValidationError(Exception):
    """Raised when input validation fails."""
    
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


async def validate_file_upload(file: UploadFile) -> None:
    """Validate an uploaded file before processing."""
    
    # 1. Check filename
    if not file.filename:
        raise InputValidationError("No filename provided", "missing_filename")
    
    # 2. Read file content for validation
    content = await file.read()
    file_size = len(content)
    
    # 3. Check file size
    if file_size == 0:
        raise InputValidationError("File is empty", "empty_file")
    
    if file_size > MAX_FILE_SIZE:
        raise InputValidationError(
            f"File too large: {file_size} bytes (max: {MAX_FILE_SIZE})",
            "file_too_large"
        )
    
    # 4. Verify MIME type (not just extension)
    detected_mime = magic.from_buffer(content[:2048], mime=True)
    
    if detected_mime not in ALLOWED_MIME_TYPES:
        raise InputValidationError(
            f"Unsupported file type: {detected_mime}",
            "unsupported_file_type"
        )
    
    # 5. Check size by type
    if detected_mime.startswith("image/") and file_size > MAX_IMAGE_SIZE:
        raise InputValidationError(
            f"Image too large: {file_size} bytes (max: {MAX_IMAGE_SIZE})",
            "image_too_large"
        )
    
    if detected_mime == "application/pdf" and file_size > MAX_PDF_SIZE:
        raise InputValidationError(
            f"PDF too large: {file_size} bytes (max: {MAX_PDF_SIZE})",
            "pdf_too_large"
        )
    
    # 6. Reset file position for downstream processing
    await file.seek(0)


def validate_image_dimensions(image_bytes: bytes, media_type: str) -> tuple[int, int]:
    """Validate image dimensions and return (width, height)."""
    from PIL import Image
    import io
    
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            
            if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                raise InputValidationError(
                    f"Image dimensions too large: {width}x{height} "
                    f"(max: {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT})",
                    "image_dimensions_too_large"
                )
            
            return width, height
    except Exception as e:
        if isinstance(e, InputValidationError):
            raise
        raise InputValidationError(
            f"Invalid image file: {e}",
            "invalid_image"
        )
```

#### 1.2 Rate Limiting

**File:** `api/app/guards/rate_limiter.py`

```python
"""Rate limiting guards for API requests."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""
    max_requests: int = 100
    window_seconds: int = 60
    max_concurrent: int = 5
    burst_limit: int = 10


@dataclass
class RateLimitState:
    """State tracking for rate limiting."""
    requests: list[float] = field(default_factory=list)
    concurrent: int = 0


class RateLimiter:
    """In-memory rate limiter for API requests."""
    
    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._states: dict[str, RateLimitState] = defaultdict(RateLimitState)
    
    def _cleanup_old_requests(self, state: RateLimitState) -> None:
        """Remove requests outside the current window."""
        cutoff = time.time() - self.config.window_seconds
        state.requests = [t for t in state.requests if t > cutoff]
    
    def check_rate_limit(self, client_id: str) -> tuple[bool, Optional[str]]:
        """Check if request is allowed. Returns (allowed, reason)."""
        state = self._states[client_id]
        self._cleanup_old_requests(state)
        
        # Check window-based limit
        if len(state.requests) >= self.config.max_requests:
            return False, f"Rate limit exceeded: {self.config.max_requests} requests per {self.config.window_seconds}s"
        
        # Check concurrent limit
        if state.concurrent >= self.config.max_concurrent:
            return False, f"Concurrent request limit exceeded: {self.config.max_concurrent}"
        
        return True, None
    
    def record_request(self, client_id: str) -> None:
        """Record a new request."""
        state = self._states[client_id]
        state.requests.append(time.time())
        state.concurrent += 1
    
    def record_completion(self, client_id: str) -> None:
        """Record request completion."""
        state = self._states[client_id]
        state.concurrent = max(0, state.concurrent - 1)
    
    def get_retry_after(self, client_id: str) -> int:
        """Get seconds until next request is allowed."""
        state = self._states[client_id]
        self._cleanup_old_requests(state)
        
        if len(state.requests) < self.config.max_requests:
            return 0
        
        oldest_in_window = min(state.requests)
        return int(oldest_in_window + self.config.window_seconds - time.time()) + 1
```

#### 1.3 Content Size Guards

**File:** `api/app/guards/content_guard.py`

```python
"""Content size and validation guards."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContentLimits(BaseModel):
    """Configurable limits for content processing."""
    
    # Document text limits
    max_document_chars: int = Field(default=50000, description="Maximum characters in document text")
    max_prompt_chars: int = Field(default=12000, description="Maximum characters in LLM prompts")
    
    # Field value limits
    max_field_name_length: int = Field(default=100, description="Maximum field name length")
    max_field_value_length: int = Field(default=1000, description="Maximum field value length")
    max_source_span_length: int = Field(default=500, description="Maximum source span length")
    
    # Response limits
    max_extraction_fields: int = Field(default=100, description="Maximum fields per extraction")
    max_validation_errors: int = Field(default=50, description="Maximum validation errors")


DEFAULT_LIMITS = ContentLimits()


def validate_document_text(text: str, limits: ContentLimits | None = None) -> str:
    """Validate and truncate document text."""
    limits = limits or DEFAULT_LIMITS
    
    if len(text) > limits.max_document_chars:
        return text[:limits.max_document_chars] + "…[truncated]"
    
    return text


def validate_field_name(name: str, limits: ContentLimits | None = None) -> str | None:
    """Validate field name length and format."""
    limits = limits or DEFAULT_LIMITS
    
    if not name or not name.strip():
        return None
    
    name = name.strip()
    
    if len(name) > limits.max_field_name_length:
        return None  # Skip fields with overly long names
    
    # Only allow snake_case alphanumeric
    import re
    if not re.match(r'^[a-z][a-z0-9_]*$', name):
        return None
    
    return name


def validate_field_value(value: str | float | None, limits: ContentLimits | None = None) -> str | float | None:
    """Validate field value length."""
    limits = limits or DEFAULT_LIMITS
    
    if value is None:
        return None
    
    if isinstance(value, str):
        if len(value) > limits.max_field_value_length:
            return value[:limits.max_field_value_length] + "…[truncated]"
        return value
    
    return value
```

---

### Phase 2: Processing Guards (Week 2-3)

#### 2.1 Stage Timeouts

**File:** `api/app/guards/timeout_guard.py`

```python
"""Timeout guards for pipeline stages."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class StageTimeoutConfig:
    """Timeout configuration for each pipeline stage."""
    router_seconds: float = 30.0
    extractor_seconds: float = 60.0
    validator_seconds: float = 10.0
    judge_seconds: float = 45.0
    ocr_seconds: float = 120.0


class StageTimeoutError(Exception):
    """Raised when a pipeline stage exceeds its timeout."""
    
    def __init__(self, stage: str, timeout: float):
        self.stage = stage
        self.timeout = timeout
        super().__init__(f"Stage '{stage}' timed out after {timeout}s")


@asynccontextmanager
async def stage_timeout(stage: str, timeout: float):
    """Context manager that enforces a timeout on async operations."""
    try:
        yield
    except asyncio.TimeoutError:
        raise StageTimeoutError(stage, timeout)


async def run_with_timeout(
    coro: Callable,
    stage: str,
    timeout: float,
    *args: Any,
    **kwargs: Any
) -> Any:
    """Run a coroutine with timeout enforcement."""
    try:
        return await asyncio.wait_for(coro(*args, **kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        raise StageTimeoutError(stage, timeout)
```

#### 2.2 Audit Logging

**File:** `api/app/guards/audit_logger.py`

```python
"""Audit logging for security events and pipeline execution."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class SecurityEventType(str, Enum):
    """Types of security events to log."""
    INJECTION_ATTEMPT = "injection_attempt"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    FILE_VALIDATION_FAILED = "file_validation_failed"
    PII_DETECTED = "pii_detected"
    HALLUCINATION_FLAGGED = "hallucination_flagged"
    STAGE_TIMEOUT = "stage_timeout"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    SUSPICIOUS_OUTPUT = "suspicious_output"


@dataclass
class SecurityEvent:
    """Security event for audit logging."""
    event_id: str
    event_type: SecurityEventType
    timestamp: float
    client_id: str
    details: dict[str, Any]
    severity: str = "warning"  # info, warning, error, critical


class AuditLogger:
    """Structured audit logger for security events."""
    
    def __init__(self, log_file: str | None = None):
        self.logger = logging.getLogger("security.audit")
        self.log_file = log_file
        
        # Set up file handler if specified
        if log_file:
            handler = logging.FileHandler(log_file)
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)
    
    def log_security_event(
        self,
        event_type: SecurityEventType,
        client_id: str,
        details: dict[str, Any],
        severity: str = "warning"
    ) -> str:
        """Log a security event and return the event ID."""
        event = SecurityEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=time.time(),
            client_id=client_id,
            details=details,
            severity=severity
        )
        
        # Log based on severity
        log_msg = json.dumps(asdict(event), default=str)
        
        if severity == "critical":
            self.logger.critical(log_msg)
        elif severity == "error":
            self.logger.error(log_msg)
        elif severity == "warning":
            self.logger.warning(log_msg)
        else:
            self.logger.info(log_msg)
        
        return event.event_id
    
    def log_injection_attempt(
        self,
        client_id: str,
        text: str,
        pattern_matched: str
    ) -> str:
        """Log a prompt injection attempt."""
        return self.log_security_event(
            SecurityEventType.INJECTION_ATTEMPT,
            client_id,
            {"text_preview": text[:200], "pattern": pattern_matched},
            severity="warning"
        )
    
    def log_pii_detected(
        self,
        client_id: str,
        field_name: str,
        pii_type: str
    ) -> str:
        """Log PII detection in extraction results."""
        return self.log_security_event(
            SecurityEventType.PII_DETECTED,
            client_id,
            {"field_name": field_name, "pii_type": pii_type},
            severity="info"
        )
    
    def log_rate_limit_exceeded(
        self,
        client_id: str,
        limit: int,
        window: int
    ) -> str:
        """Log rate limit exceeded."""
        return self.log_security_event(
            SecurityEventType.RATE_LIMIT_EXCEEDED,
            client_id,
            {"limit": limit, "window_seconds": window},
            severity="warning"
        )
```

#### 2.3 PII Detection

**File:** `api/app/guards/pii_detector.py`

```python
"""PII detection for extracted field values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PIIType(str, Enum):
    """Types of PII to detect."""
    CREDIT_CARD = "credit_card"
    NATIONAL_ID = "national_id"
    EMAIL = "email"
    PHONE = "phone"
    BANK_ACCOUNT = "bank_account"
    TAX_ID = "tax_id"


@dataclass
class PIIMatch:
    """A detected PII match."""
    pii_type: PIIType
    value: str
    start: int
    end: int
    confidence: float


# PII detection patterns
PII_PATTERNS = {
    PIIType.CREDIT_CARD: {
        "pattern": r'\b(?:\d[ -]*?){13,16}\b',
        "description": "Credit card number",
        "redact": True,
    },
    PIIType.NATIONAL_ID: {
        "pattern": r'\b\d{1}-\d{4}-\d{5}-\d{2}-\d{1}\b',  # Thai National ID
        "description": "National ID number",
        "redact": True,
    },
    PIIType.EMAIL: {
        "pattern": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        "description": "Email address",
        "redact": False,
    },
    PIIType.PHONE: {
        "pattern": r'\b(?:\+?66|0)\d{8,9}\b',  # Thai phone numbers
        "description": "Phone number",
        "redact": False,
    },
    PIIType.TAX_ID: {
        "pattern": r'\b\d{13}\b',  # Thai Tax ID (13 digits)
        "description": "Tax ID number",
        "redact": True,
    },
}


class PIIDetector:
    """Detect PII in text and field values."""
    
    def __init__(self, redact_pii: bool = False):
        self.redact_pii = redact_pii
        self._compiled_patterns = {
            pii_type: re.compile(info["pattern"], re.IGNORECASE)
            for pii_type, info in PII_PATTERNS.items()
        }
    
    def detect(self, text: str) -> list[PIIMatch]:
        """Detect all PII in text."""
        matches = []
        
        for pii_type, pattern in self._compiled_patterns.items():
            for match in pattern.finditer(text):
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    value=match.group(),
                    start=match.start(),
                    end=match.end(),
                    confidence=0.9
                ))
        
        return matches
    
    def redact(self, text: str) -> str:
        """Redact detected PII from text."""
        matches = sorted(self.detect(text), key=lambda m: m.start, reverse=True)
        
        for match in matches:
            if PII_PATTERNS[match.pii_type]["redact"]:
                redacted = f"[REDACTED_{match.pii_type.value.upper()}]"
                text = text[:match.start] + redacted + text[match.end:]
        
        return text
    
    def check_field_value(self, field_name: str, value: str | None) -> list[PIIMatch]:
        """Check a field value for PII."""
        if not value or not isinstance(value, str):
            return []
        
        return self.detect(value)
```

---

### Phase 3: Output Guards (Week 3-4)

#### 3.1 Response Schema Validation

**File:** `api/app/guards/output_guard.py`

```python
"""Output validation guards for LLM responses."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar, Type

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OutputValidationError(Exception):
    """Raised when output validation fails."""
    
    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


def clean_llm_output(raw_text: str) -> str:
    """Clean common LLM output formatting issues."""
    if not raw_text:
        return ""
    
    text = raw_text.strip()
    
    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(
            line for line in lines[1:]
            if not line.strip().startswith("```")
        )
        text = inner.strip()
    
    # Remove any leading/trailing prose (find first { or [)
    json_start = -1
    for i, char in enumerate(text):
        if char in ('{', '['):
            json_start = i
            break
    
    if json_start > 0:
        text = text[json_start:]
    
    return text


def validate_output_schema(
    raw_text: str,
    schema: Type[T],
    strict: bool = True
) -> T:
    """Validate LLM output against a Pydantic schema.
    
    Args:
        raw_text: Raw LLM response text
        schema: Pydantic model class to validate against
        strict: If True, raise on validation failure
        
    Returns:
        Validated Pydantic model instance
        
    Raises:
        OutputValidationError: If validation fails and strict=True
    """
    cleaned = clean_llm_output(raw_text)
    
    if not cleaned:
        if strict:
            raise OutputValidationError("Empty LLM output", raw_text)
        return schema()  # Return default instance
    
    # Try JSON parsing
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        if strict:
            raise OutputValidationError(
                f"Invalid JSON in LLM output: {e}",
                raw_text
            )
        logger.warning("LLM output JSON parse failed, returning default: %s", e)
        return schema()
    
    # Validate against schema
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        if strict:
            raise OutputValidationError(
                f"Schema validation failed: {e}",
                raw_text
            )
        logger.warning("LLM output schema validation failed: %s", e)
        return schema()


def sanitize_error_message(error: Exception, include_details: bool = False) -> str:
    """Sanitize error messages to prevent information leakage."""
    error_str = str(error)
    
    # Remove file paths
    error_str = re.sub(r'(/[a-zA-Z0-9_.-]+)+', '[PATH]', error_str)
    
    # Remove API keys
    error_str = re.sub(r'(api[_-]?key|secret|token)[=:]\s*\S+', r'\1=[REDACTED]', error_str, flags=re.IGNORECASE)
    
    # Remove hostnames
    error_str = re.sub(r'localhost:\d+', '[HOST]', error_str)
    error_str = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+', '[HOST]', error_str)
    
    if not include_details:
        # Generic error messages for clients
        if "timeout" in error_str.lower():
            return "Request timed out. Please try again."
        elif "rate limit" in error_str.lower():
            return "Too many requests. Please try again later."
        elif "validation" in error_str.lower():
            return "Invalid request data."
    
    return error_str
```

---

## Implementation Plan

### Week 1: Input Guards

| Day | Task | Files |
|-----|------|-------|
| 1-2 | Create `input_guard.py` with file validation | `api/app/guards/input_guard.py` |
| 3 | Create `rate_limiter.py` with rate limiting | `api/app/guards/rate_limiter.py` |
| 4 | Create `content_guard.py` with size limits | `api/app/guards/content_guard.py` |
| 5 | Integrate guards into `/api/extract` endpoint | `api/app/api/routes.py` |

### Week 2: Processing Guards

| Day | Task | Files |
|-----|------|-------|
| 1-2 | Create `timeout_guard.py` with stage timeouts | `api/app/guards/timeout_guard.py` |
| 3 | Create `audit_logger.py` for security events | `api/app/guards/audit_logger.py` |
| 4 | Create `pii_detector.py` for PII detection | `api/app/guards/pii_detector.py` |
| 5 | Integrate guards into extraction pipeline | `api/app/services/extraction_service.py` |

### Week 3: Output Guards

| Day | Task | Files |
|-----|------|-------|
| 1-2 | Create `output_guard.py` with schema validation | `api/app/guards/output_guard.py` |
| 3 | Add error sanitization to all endpoints | `api/app/api/routes.py` |
| 4 | Integrate PII detection into output flow | `api/app/services/extraction_service.py` |
| 5 | Update Langfuse tracing with security events | `api/app/observability/langfuse.py` |

### Week 4: Testing & Documentation

| Day | Task | Files |
|-----|------|-------|
| 1-2 | Write unit tests for all guards | `api/tests/test_guards.py` |
| 3 | Write integration tests | `api/tests/test_guard_integration.py` |
| 4 | Update configuration documentation | `docs/configuration.md` |
| 5 | Update security documentation | `docs/security.md` |

---

## Code Examples

### Integration in Routes

```python
# api/app/api/routes.py

from app.guards.input_guard import validate_file_upload, InputValidationError
from app.guards.rate_limiter import RateLimiter
from app.guards.audit_logger import AuditLogger, SecurityEventType

# Initialize guards
rate_limiter = RateLimiter()
audit_logger = AuditLogger()

@router.post("/extract")
async def extract_documents(files: list[UploadFile]):
    # Get client ID (from IP or auth)
    client_id = request.client.host if request.client else "unknown"
    
    # Check rate limit
    allowed, reason = rate_limiter.check_rate_limit(client_id)
    if not allowed:
        audit_logger.log_rate_limit_exceeded(client_id, 100, 60)
        raise HTTPException(status_code=429, detail=reason)
    
    rate_limiter.record_request(client_id)
    
    try:
        # Validate each file
        for file in files:
            try:
                await validate_file_upload(file)
            except InputValidationError as e:
                audit_logger.log_file_validation_failed(client_id, file.filename, e.code)
                raise HTTPException(status_code=400, detail=str(e))
        
        # Process extraction
        result = await extraction_service.extract_group(parts)
        return result
        
    finally:
        rate_limiter.record_completion(client_id)
```

### Integration in Extraction Service

```python
# api/app/services/extraction_service.py

from app.guards.timeout_guard import StageTimeoutConfig, stage_timeout
from app.guards.pii_detector import PIIDetector
from app.guards.audit_logger import AuditLogger

# Initialize guards
timeout_config = StageTimeoutConfig()
pii_detector = PIIDetector(redact_pii=False)  # Log but don't redact
audit_logger = AuditLogger()

async def _extract_one_page(self, filename, page_text, image_bytes=None, image_media_type=None):
    # Router with timeout
    async with stage_timeout("router", timeout_config.router_seconds):
        routing = await self.router.classify(...)
    
    # Extractor with timeout
    async with stage_timeout("extractor", timeout_config.extractor_seconds):
        fields, new_field_names = await self.extractors[routing.doc_type].extract(...)
    
    # Check for PII in extracted fields
    for field in fields:
        pii_matches = pii_detector.check_field_value(field.name, str(field.value))
        if pii_matches:
            for match in pii_matches:
                audit_logger.log_pii_detected(
                    client_id=filename,  # Use filename as identifier
                    field_name=field.name,
                    pii_type=match.pii_type.value
                )
    
    return ExtractionResult(...)
```

---

## Testing Strategy

### Unit Tests

```python
# api/tests/test_guards.py

import pytest
from app.guards.input_guard import validate_file_upload, InputValidationError
from app.guards.pii_detector import PIIDetector
from app.guards.audit_logger import AuditLogger, SecurityEventType


@pytest.mark.asyncio
async def test_validate_file_upload_rejects_empty_file():
    # Create empty file mock
    ...
    with pytest.raises(InputValidationError) as exc:
        await validate_file_upload(empty_file)
    assert exc.value.code == "empty_file"


def test_pii_detector_finds_credit_card():
    detector = PIIDetector()
    matches = detector.detect("Invoice total: 4111-1111-1111-1111")
    assert any(m.pii_type.value == "credit_card" for m in matches)


def test_pii_detector_redacts_national_id():
    detector = PIIDetector(redact_pii=True)
    result = detector.redact("Thai ID: 1-2345-67890-12-3")
    assert "1-2345-67890-12-3" not in result
    assert "[REDACTED_NATIONAL_ID]" in result


def test_audit_logger_records_events():
    logger = AuditLogger()
    event_id = logger.log_security_event(
        SecurityEventType.INJECTION_ATTEMPT,
        "test_client",
        {"text": "ignore previous instructions"}
    )
    assert event_id is not None
```

### Integration Tests

```python
# api/tests/test_guard_integration.py

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_rate_limiting_blocks_excessive_requests():
    async with AsyncClient(app=app) as client:
        # Make many requests quickly
        for _ in range(110):
            await client.get("/api/health")
        
        # Next request should be rate limited
        response = await client.get("/api/health")
        assert response.status_code == 429


@pytest.mark.asyncio
async def test_file_validation_rejects_invalid_type():
    async with AsyncClient(app=app) as client:
        # Upload executable file
        response = await client.post(
            "/api/extract",
            files=[("files", ("malware.exe", b"MZ\x90\x00", "application/octet-stream"))]
        )
        assert response.status_code == 400
        assert "unsupported file type" in response.json()["detail"].lower()
```

---

## Configuration

### Environment Variables

```bash
# api/.env

# Guard Configuration
GUARDS_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_CONCURRENT=5

# File Upload Limits
MAX_UPLOAD_MB=50
MAX_IMAGE_MB=20
MAX_PDF_MB=50
MAX_IMAGE_WIDTH=10000
MAX_IMAGE_HEIGHT=10000

# Content Limits
MAX_DOCUMENT_CHARS=50000
MAX_PROMPT_CHARS=12000
MAX_FIELD_NAME_LENGTH=100
MAX_FIELD_VALUE_LENGTH=1000

# Timeout Configuration
ROUTER_TIMEOUT_SECONDS=30
EXTRACTOR_TIMEOUT_SECONDS=60
JUDGE_TIMEOUT_SECONDS=45
OCR_TIMEOUT_SECONDS=120

# PII Detection
PII_DETECTION_ENABLED=true
PII_REDACT_IN_LOGS=true

# Audit Logging
AUDIT_LOG_ENABLED=true
AUDIT_LOG_FILE=logs/security_audit.jsonl
```

### Settings Class Updates

```python
# api/app/core/config.py

class Settings:
    def __init__(self) -> None:
        # ... existing settings ...
        
        # Guard Configuration
        self.guards_enabled = os.getenv("GUARDS_ENABLED", "true").lower() == "true"
        self.rate_limit_max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))
        self.rate_limit_window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.rate_limit_max_concurrent = int(os.getenv("RATE_LIMIT_MAX_CONCURRENT", "5"))
        
        # Timeout Configuration
        self.router_timeout_seconds = float(os.getenv("ROUTER_TIMEOUT_SECONDS", "30"))
        self.extractor_timeout_seconds = float(os.getenv("EXTRACTOR_TIMEOUT_SECONDS", "60"))
        self.judge_timeout_seconds = float(os.getenv("JUDGE_TIMEOUT_SECONDS", "45"))
        self.ocr_timeout_seconds = float(os.getenv("OCR_TIMEOUT_SECONDS", "120"))
        
        # PII Detection
        self.pii_detection_enabled = os.getenv("PII_DETECTION_ENABLED", "true").lower() == "true"
        self.pii_redact_in_logs = os.getenv("PII_REDACT_IN_LOGS", "true").lower() == "true"
        
        # Audit Logging
        self.audit_log_enabled = os.getenv("AUDIT_LOG_ENABLED", "true").lower() == "true"
        self.audit_log_file = os.getenv("AUDIT_LOG_FILE", "logs/security_audit.jsonl")
```

---

## Summary

This implementation plan provides a comprehensive AI guardrails system with:

1. **Input Guards**: File validation, rate limiting, content size limits
2. **Processing Guards**: Stage timeouts, audit logging, PII detection
3. **Output Guards**: Schema validation, error sanitization

The guards integrate seamlessly with your existing security architecture and can be enabled/disabled via configuration. All guards are designed to be non-blocking by default (can be set to strict mode for production).

**Next Steps:**
1. Review and approve this plan
2. Create the `api/app/guards/` directory
3. Implement guards following the weekly schedule
4. Add comprehensive tests
5. Update documentation
