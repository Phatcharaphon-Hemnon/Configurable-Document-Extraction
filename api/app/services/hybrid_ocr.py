"""Hybrid OCR orchestration: detect once → TH rec → EN retry → TrOCR retry.

Pipeline per page (CPU, sequential):

1. Detect text regions ONCE (PP-OCRv5 mobile Det) + Thai recognition first
   (PP-OCRv5 mobile TH — covers Thai+English). Retain 4-point polygons.
2. Crop + correct orientation per region (perspective + 180° fix).
3. Retry English recognition (PP-OCRv5 mobile EN). Confident Thai print
   keeps TH; uncertain regions retry EN even when the TH reading looks
   Thai — handwriting misread as Thai salad must not block an English
   attempt. Retain conflicting readings — never assume cross-model
   calibration.
4. Selective TrOCR retry (``microsoft/trocr-base-handwritten``, LINE crops
   only) permitted only where the English candidate supports Latin text:
   nonempty Latin-containing EN reading, Latin selected text without Thai,
   selected RapidOCR confidence < threshold (default 0.80). Skips empty,
   digit-only, ambiguous-script. At most N lowest-confidence regions per
   page (default 10), sequential CPU. Different nonempty Latin TrOCR
   readings are used provisionally + flagged for review; generation scores
   are never extraction confidence.
5. Emit ``OCRBlock`` list (selected text + alternatives + review reasons)
   for ``layout_text`` (table gaps preserved) and page ``review_reasons``
   for ``needs_review`` propagation.

Thai handwriting remains unsupported (guaranteed capability: printed
Thai/English + uncertain English handwriting assistance only).

Failure handling: EN/TrOCR failures preserve RapidOCR text + review reason;
primary detection failure raises (caller keeps existing page-error path).
Bounded by the caller's OCR timeout (deadline checked before each TrOCR).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.schemas.ocr import AlternativeReading, OCRBlock

logger = logging.getLogger(__name__)

_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def contains_thai(text: str | None) -> bool:
    return bool(text and _THAI_RE.search(text))


def contains_latin(text: str | None) -> bool:
    return bool(text and _LATIN_RE.search(text))


def is_digit_only(text: str) -> bool:
    """True when the text has no letters at all (digits/symbols only)."""
    s = (text or "").strip()
    if not s:
        return False
    return not any(c.isalpha() for c in s)


def is_ambiguous_script(text: str) -> bool:
    """True when letters exist outside Latin + Thai (e.g. CJK/Arabic).

    Selective retry only — not a handwriting classifier.
    """
    for c in text or "":
        if c.isalpha() and not ("A" <= c <= "Z" or "a" <= c <= "z" or "\u0e00" <= c <= "\u0e7f"):
            return True
    return False


def is_trocr_eligible(
    selected_text: str,
    th_text: str,
    en_text: str | None,
    selected_conf: float,
    *,
    threshold: float,
) -> bool:
    """Eligibility for TrOCR selective retry.

    TrOCR is permitted only where the ENGLISH candidate supports Latin
    text: ``en_text`` must be nonempty and Latin-containing. A Thai-looking
    TH reading never vetoes recovery on its own (handwriting misread as
    Thai salad is the recovery case); what matters is that the selected
    and English readings are Latin without Thai, not digit-only, not
    ambiguous-script, and below the confidence threshold. ``th_text`` is
    retained for signature compatibility.
    """
    _ = th_text
    sel = (selected_text or "").strip()
    en = (en_text or "").strip()
    if not sel or not en:
        return False
    if not contains_latin(en):
        return False
    if not contains_latin(sel):
        return False
    if contains_thai(sel) or contains_thai(en):
        return False
    if is_digit_only(sel):
        return False
    if is_ambiguous_script(sel) or is_ambiguous_script(en):
        return False
    try:
        if float(selected_conf) >= float(threshold):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _axis_box(poly: Any) -> tuple[float, float, float, float]:
    try:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        return (x0, y0, x1 - x0, y1 - y0)
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def hybrid_ocr_page(
    image_bytes: bytes,
    *,
    settings: Any,
    rapid_client: Any,
    trocr_client: Any | None = None,
    deadline: float | None = None,
) -> tuple[list[OCRBlock], list[str], list[str], dict[str, str]]:
    """Run hybrid OCR on one page image.

    Returns ``(blocks, engines_used, review_reasons, model_revisions)``.
    Caller renders ``blocks`` via ``layout_text`` (table gaps preserved) and
    feeds only the selected text into extraction; ``review_reasons`` propagate
    to ``validation_errors``/``needs_review``.

    Raises on primary detection failure (caller uses page-error path).
    EN/TrOCR failures never raise — they preserve RapidOCR + review reason.
    """
    threshold = float(getattr(settings, "hybrid_trocr_conf_threshold", 0.80))
    max_regions = int(getattr(settings, "hybrid_trocr_max_regions", 10))
    preprocess_version = str(getattr(settings, "hybrid_preprocess_version", "hybrid-v1"))

    # 1) Detect once + TH recognition.
    detailed = rapid_client.ocr_detailed(image_bytes)
    boxes = detailed.boxes
    txts_th: list[str] = list(detailed.txts_th or [])
    scores_th: list[float] = list(detailed.scores_th or [])
    image = detailed.image

    try:
        n = len(txts_th)
    except Exception:
        n = 0
    if n == 0:
        revs = rapid_client.model_revisions() if hasattr(rapid_client, "model_revisions") else {}
        revs = dict(revs)
        revs["hybrid_preprocess"] = preprocess_version
        revs["hybrid_threshold"] = str(threshold)
        revs["hybrid_max_regions"] = str(max_regions)
        if trocr_client is not None and hasattr(trocr_client, "model_revision_info"):
            try:
                revs.update(trocr_client.model_revision_info())
            except Exception:
                pass
        return [], ["rapidocr-th"], [], revs

    # Per-region state after EN stage.
    selected_texts: list[str] = [""] * n
    selected_confs: list[float] = [0.0] * n
    selected_engines: list[str] = ["rapidocr-th"] * n
    alternatives: list[list[AlternativeReading]] = [[] for _ in range(n)]
    region_reviews: list[str | None] = [None] * n
    crops: list[Any | None] = [None] * n
    th_texts: list[str] = [str(t or "") for t in txts_th]
    en_texts: list[str | None] = [None] * n
    en_confs: list[float] = [0.0] * n

    engines_used: list[str] = ["rapidocr-th"]
    page_reviews: list[str] = []

    # 2-3) Crop + EN retry on non-Thai regions.
    for i in range(n):
        th_text = th_texts[i]
        try:
            conf_th = float(scores_th[i]) if i < len(scores_th) else 0.0
        except (TypeError, ValueError):
            conf_th = 0.0
        selected_texts[i] = th_text
        selected_confs[i] = conf_th

        try:
            poly = boxes[i]
        except Exception:
            poly = None
        crop = None
        if poly is not None:
            try:
                crop = rapid_client.crop_region(image, poly)
            except Exception as exc:  # noqa: BLE001 — crop failure keeps TH.
                logger.warning("Hybrid crop %d failed: %s", i, exc)
                crop = None
        crops[i] = crop

        # Confident Thai print keeps TH with no EN retry. Uncertain regions
        # run the EN retry even when the TH reading looks Thai: handwriting
        # misrecognised as Thai script must not block an English attempt.
        # The uncertainty boundary is the configured TrOCR threshold.
        if contains_thai(th_text) and conf_th >= threshold:
            continue
        # Empty TH with no crop: nothing to retry.
        if not (th_text or "").strip() and crop is None:
            continue
        # Non-Thai: EN retry (cheap ONNX, all non-Thai regions).
        if crop is None:
            continue
        try:
            en_txts, en_scores = rapid_client.recognize_crops([crop], lang="en")
            en_text = str(en_txts[0] if en_txts else "" or "")
            try:
                en_conf = float(en_scores[0]) if en_scores else 0.0
            except (TypeError, ValueError):
                en_conf = 0.0
        except Exception as exc:  # noqa: BLE001 — EN failure keeps TH.
            logger.warning("Hybrid EN retry %d failed: %s", i, exc)
            region_reviews[i] = "en retry failed — kept Thai-model reading"
            continue
        en_texts[i] = en_text
        en_confs[i] = en_conf
        if "rapidocr-en" not in engines_used:
            engines_used.append("rapidocr-en")

        th_s = (th_text or "").strip()
        en_s = (en_text or "").strip()
        if not th_s and en_s:
            selected_texts[i] = en_text
            selected_confs[i] = en_conf
            selected_engines[i] = "rapidocr-en"
        elif th_s and not en_s:
            pass  # keep TH
        elif not th_s and not en_s:
            pass  # both empty — skipped later
        elif th_s == en_s:
            pass  # agreement — keep TH
        else:
            # Conflict: retain both readings, provisionally prefer EN, flag
            # for review. Never assume cross-model calibration. EN is
            # preferred because it is specialised for Latin script and this
            # branch only runs for uncertain regions.
            selected_texts[i] = en_text
            selected_confs[i] = en_conf
            selected_engines[i] = "rapidocr-en"
            alternatives[i].append(
                AlternativeReading(text=th_text, engine="rapidocr-th", confidence=conf_th)
            )
            region_reviews[i] = f"conflicting RapidOCR readings kept: th={th_s[:40]!r} en={en_s[:40]!r}"

    # 4) Selective TrOCR retry on eligible low-confidence English regions.
    eligible: list[int] = []
    for i in range(n):
        sel = selected_texts[i]
        th_t = th_texts[i]
        en_t = en_texts[i]
        if is_trocr_eligible(sel, th_t, en_t, selected_confs[i], threshold=threshold):
            if crops[i] is None:
                continue
            eligible.append(i)
    eligible.sort(key=lambda i: selected_confs[i])
    trocr_targets = eligible[: max(0, max_regions)]

    trocr_used = False
    trocr_failures = 0
    trocr_provisional = 0
    if trocr_targets:
        # Lazily construct the client when the caller did not inject one.
        if trocr_client is None:
            try:
                from app.services.trocr_client import TrOCRClient as _TrOCRClient

                trocr_client = _TrOCRClient(
                    model_name=getattr(settings, "hybrid_trocr_model", "microsoft/trocr-base-handwritten"),
                    revision=getattr(settings, "hybrid_trocr_revision", None) or None,
                )
            except Exception as exc:  # noqa: BLE001 — no TrOCR, keep RapidOCR.
                logger.warning("Hybrid TrOCR unavailable: %s", exc)
                page_reviews.append(f"hybrid: trocr unavailable ({exc}) — kept RapidOCR readings")
                trocr_client = None
        if trocr_client is not None:
            if "trocr" not in engines_used:
                engines_used.append("trocr")
            for idx in trocr_targets:
                if deadline is not None and time.monotonic() > deadline - 2.0:
                    page_reviews.append("hybrid: trocr stopped early to respect OCR timeout")
                    break
                crop = crops[idx]
                if crop is None:
                    continue
                try:
                    out = trocr_client.transcribe_crops([crop])
                    trocr_text = str(out[0] if out else "" or "").strip()
                except Exception as exc:  # noqa: BLE001 — failed retry keeps RapidOCR.
                    logger.warning("Hybrid TrOCR %d failed: %s", idx, exc)
                    trocr_failures += 1
                    prev = region_reviews[idx]
                    region_reviews[idx] = ((prev + "; ") if prev else "") + "trocr retry failed — kept RapidOCR reading"
                    continue
                trocr_used = True
                sel = (selected_texts[idx] or "").strip()
                if not trocr_text:
                    trocr_failures += 1
                    prev = region_reviews[idx]
                    region_reviews[idx] = ((prev + "; ") if prev else "") + "trocr empty — kept RapidOCR reading"
                    continue
                if not contains_latin(trocr_text):
                    prev = region_reviews[idx]
                    region_reviews[idx] = ((prev + "; ") if prev else "") + "trocr non-Latin — kept RapidOCR reading"
                    continue
                if trocr_text == sel:
                    # Agreement — still uncertain (low RapidOCR conf), keep
                    # RapidOCR text but note the corroboration.
                    prev = region_reviews[idx]
                    region_reviews[idx] = ((prev + "; ") if prev else "") + "trocr agrees with RapidOCR (low confidence kept)"
                    continue
                # Different nonempty Latin reading: provisional use + review.
                # Generation scores are NOT extraction confidence — keep the
                # original RapidOCR confidence.
                alternatives[idx].append(
                    AlternativeReading(
                        text=selected_texts[idx],
                        engine=selected_engines[idx],
                        confidence=selected_confs[idx],
                    )
                )
                alternatives[idx].append(
                    AlternativeReading(text=trocr_text, engine="trocr", confidence=None)
                )
                selected_texts[idx] = trocr_text
                selected_engines[idx] = "trocr"
                trocr_provisional += 1
                prev = region_reviews[idx]
                region_reviews[idx] = ((prev + "; ") if prev else "") + (
                    f"trocr provisional: {sel[:40]!r} → {trocr_text[:40]!r} — verify handwriting"
                )

    # Page-level review reasons (propagated to validation_errors/needs_review).
    n_conflict = sum(1 for r in region_reviews if r and "conflicting" in r)
    if n_conflict:
        page_reviews.append(f"hybrid: conflicting RapidOCR readings in {n_conflict} region(s) — verify")
    if trocr_targets:
        page_reviews.append(
            f"hybrid: {len(trocr_targets)} uncertain English region(s) "
            f"(rapidocr conf<{threshold:.2f}) sent to TrOCR; "
            f"{trocr_provisional} provisional, {trocr_failures} failed/empty"
        )
    if trocr_used and trocr_provisional:
        page_reviews.append("hybrid: TrOCR provisional readings used — page needs review (verify handwriting)")
    elif trocr_targets and not trocr_used:
        # TrOCR was eligible but never ran (unavailable/timeout) — uncertainty remains.
        if not any("trocr unavailable" in r or "trocr stopped" in r for r in page_reviews):
            page_reviews.append("hybrid: uncertain English regions kept RapidOCR readings — verify")

    # 5) Emit blocks (skip empty selections; unresolved empties stay silent
    # unless they were TrOCR-eligible, which already flagged the page).
    blocks: list[OCRBlock] = []
    for i in range(n):
        sel = (selected_texts[i] or "").strip()
        if not sel:
            continue
        blocks.append(
            OCRBlock(
                text=selected_texts[i],
                confidence=float(selected_confs[i]),
                box=_axis_box(boxes[i] if i < len(boxes) else (0, 0, 0, 0)),
                engine=selected_engines[i],
                alternatives=alternatives[i],
                review_reason=region_reviews[i],
            )
        )

    revs = rapid_client.model_revisions() if hasattr(rapid_client, "model_revisions") else {}
    revs = dict(revs)
    revs["hybrid_preprocess"] = preprocess_version
    revs["hybrid_threshold"] = str(threshold)
    revs["hybrid_max_regions"] = str(max_regions)
    if trocr_client is not None and hasattr(trocr_client, "model_revision_info"):
        try:
            revs.update(trocr_client.model_revision_info())
        except Exception:
            pass
    return blocks, engines_used, page_reviews, revs
