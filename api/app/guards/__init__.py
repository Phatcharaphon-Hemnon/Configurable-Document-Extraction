"""AI Guardrails for the document extraction pipeline.

This package provides security guards for input validation, processing
control, and output sanitization.
"""

from app.guards.audit_logger import AuditLogger, SecurityEventType
from app.guards.content_guard import ContentLimits, validate_document_text, validate_field_name, validate_field_value
from app.guards.input_guard import InputValidationError, validate_file_upload, validate_image_dimensions
from app.guards.output_guard import OutputValidationError, clean_llm_output, sanitize_error_message, validate_output_schema
from app.guards.pii_detector import PIIDetector, PIIMatch, PIIType
from app.guards.rate_limiter import RateLimiter, RateLimitConfig
from app.guards.timeout_guard import StageTimeoutConfig, StageTimeoutError, stage_timeout

__all__ = [
    # Input guards
    "InputValidationError",
    "validate_file_upload",
    "validate_image_dimensions",
    
    # Rate limiting
    "RateLimiter",
    "RateLimitConfig",
    
    # Content guards
    "ContentLimits",
    "validate_document_text",
    "validate_field_name",
    "validate_field_value",
    
    # Timeout guards
    "StageTimeoutConfig",
    "StageTimeoutError",
    "stage_timeout",
    
    # Audit logging
    "AuditLogger",
    "SecurityEventType",
    
    # PII detection
    "PIIDetector",
    "PIIMatch",
    "PIIType",
    
    # Output guards
    "OutputValidationError",
    "clean_llm_output",
    "validate_output_schema",
    "sanitize_error_message",
]
