"""Audit logging for security events and pipeline execution."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


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
    CONTENT_VALIDATION_FAILED = "content_validation_failed"
    EXTRACTION_FAILED = "extraction_failed"


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
    """Structured audit logger for security events.

    Logs security events in JSON format for easy parsing and analysis.
    Can optionally write to a file for persistent storage.

    Example:
        audit = AuditLogger(log_file="logs/security_audit.jsonl")
        audit.log_injection_attempt("192.168.1.1", "ignore previous instructions", "ignore")
    """

    def __init__(self, log_file: str | None = None):
        """Initialize audit logger.

        Args:
            log_file: Optional file path for persistent logging.
                      Events are written as JSON lines.
        """
        self.logger = logging.getLogger("security.audit")
        self.log_file = log_file

        # Set up file handler if specified
        if log_file:
            try:
                from pathlib import Path

                Path(log_file).parent.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(log_file)
                handler.setFormatter(logging.Formatter("%(message)s"))
                self.logger.addHandler(handler)
            except Exception as e:
                logger.warning("Failed to set up audit log file %s: %s", log_file, e)

    def log_security_event(
        self,
        event_type: SecurityEventType,
        client_id: str,
        details: dict[str, Any],
        severity: str = "warning",
    ) -> str:
        """Log a security event and return the event ID.

        Args:
            event_type: Type of security event.
            client_id: Identifier for the client.
            details: Additional event details.
            severity: Event severity (info, warning, error, critical).

        Returns:
            Unique event ID for tracking.
        """
        event = SecurityEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            timestamp=time.time(),
            client_id=client_id,
            details=details,
            severity=severity,
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
        pattern_matched: str,
    ) -> str:
        """Log a prompt injection attempt.

        Args:
            client_id: Identifier for the client.
            text: The suspicious text (truncated for logging).
            pattern_matched: The injection pattern that was detected.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.INJECTION_ATTEMPT,
            client_id,
            {
                "text_preview": text[:200] + "..." if len(text) > 200 else text,
                "pattern": pattern_matched,
            },
            severity="warning",
        )

    def log_pii_detected(
        self,
        client_id: str,
        field_name: str,
        pii_type: str,
        value_preview: str | None = None,
    ) -> str:
        """Log PII detection in extraction results.

        Args:
            client_id: Identifier for the client.
            field_name: Name of the field containing PII.
            pii_type: Type of PII detected.
            value_preview: Preview of the PII value (for debugging).

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.PII_DETECTED,
            client_id,
            {
                "field_name": field_name,
                "pii_type": pii_type,
                "value_preview": value_preview[:50] if value_preview else None,
            },
            severity="info",
        )

    def log_rate_limit_exceeded(
        self,
        client_id: str,
        limit: int,
        window: int,
    ) -> str:
        """Log rate limit exceeded.

        Args:
            client_id: Identifier for the client.
            limit: Request limit that was exceeded.
            window: Time window in seconds.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.RATE_LIMIT_EXCEEDED,
            client_id,
            {"limit": limit, "window_seconds": window},
            severity="warning",
        )

    def log_file_validation_failed(
        self,
        client_id: str,
        filename: str,
        error_code: str,
        details: str | None = None,
    ) -> str:
        """Log file validation failure.

        Args:
            client_id: Identifier for the client.
            filename: Name of the file that failed validation.
            error_code: Validation error code.
            details: Additional error details.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.FILE_VALIDATION_FAILED,
            client_id,
            {
                "filename": filename,
                "error_code": error_code,
                "details": details,
            },
            severity="warning",
        )

    def log_hallucination_flagged(
        self,
        client_id: str,
        field_name: str,
        source_span: str | None,
        document_text: str | None,
    ) -> str:
        """Log hallucination flag from evidence check.

        Args:
            client_id: Identifier for the client.
            field_name: Name of the flagged field.
            source_span: The claimed source span.
            document_text: The original document text.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.HALLUCINATION_FLAGGED,
            client_id,
            {
                "field_name": field_name,
                "source_span_preview": (source_span[:100] if source_span else None),
                "document_preview": (document_text[:100] if document_text else None),
            },
            severity="warning",
        )

    def log_stage_timeout(
        self,
        client_id: str,
        stage: str,
        timeout: float,
        duration: float,
    ) -> str:
        """Log stage timeout.

        Args:
            client_id: Identifier for the client.
            stage: Pipeline stage that timed out.
            timeout: Configured timeout value.
            duration: Actual duration before timeout.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.STAGE_TIMEOUT,
            client_id,
            {
                "stage": stage,
                "timeout_seconds": timeout,
                "duration_seconds": duration,
            },
            severity="error",
        )

    def log_suspicious_output(
        self,
        client_id: str,
        stage: str,
        output_preview: str,
        reason: str,
    ) -> str:
        """Log suspicious output from LLM.

        Args:
            client_id: Identifier for the client.
            stage: Pipeline stage that produced suspicious output.
            output_preview: Preview of the suspicious output.
            reason: Reason why output is suspicious.

        Returns:
            Event ID.
        """
        return self.log_security_event(
            SecurityEventType.SUSPICIOUS_OUTPUT,
            client_id,
            {
                "stage": stage,
                "output_preview": output_preview[:200],
                "reason": reason,
            },
            severity="warning",
        )
