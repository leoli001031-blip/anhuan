"""Strict F0-C-compatible native PDF body extraction from a verified fd."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import logging
import os
import unicodedata
import warnings
from typing import Iterator

from ..f0e.contracts import PageRoute
from ..f0e.vault_adapter import VerifiedSourceFd
from .contracts import CanonicalBody, F0FError, native_body


_MAX_PDF_PAGES = 128
_MAX_PAGE_TEXT_CHARACTERS = 2_000_000


def extract_native_page(source: VerifiedSourceFd, route: PageRoute) -> CanonicalBody:
    """Re-extract one native page and prove F0-C hash/count identity."""

    if (
        not isinstance(source, VerifiedSourceFd)
        or not isinstance(route, PageRoute)
        or route.unit_kind != "PAGE"
        or route.evidence_method != "NATIVE_REFERENCE"
        or route.candidate_decision != "NATIVE_CANDIDATE"
        or route.native_text_sha256 is None
    ):
        raise F0FError("NATIVE_PARSE_FAILED")
    source.reverify()
    duplicate = -1
    handle = None
    try:
        duplicate = os.dup(source.fileno())
        handle = os.fdopen(duplicate, "rb", closefd=True)
        duplicate = -1
        from pypdf import PdfReader

        with _quiet_pypdf():
            reader = PdfReader(
                handle,
                strict=True,
                password=None,
                root_object_recovery_limit=10_000,
            )
            if reader.is_encrypted or not 1 <= len(reader.pages) <= _MAX_PDF_PAGES:
                raise F0FError("NATIVE_PARSE_FAILED")
            if len(reader.pages) != route.expected_total_pages:
                raise F0FError("NATIVE_TEXT_MISMATCH")
            page = reader.pages[route.page_no - 1]
            text = page.extract_text(extraction_mode="plain") or ""
        if len(text) > _MAX_PAGE_TEXT_CHARACTERS:
            raise F0FError("BODY_LIMIT_EXCEEDED")
        digest = hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()
        native_characters = sum(
            not character.isspace()
            and unicodedata.category(character) not in {"Cc", "Cf", "Co", "Cs", "Cn"}
            for character in text
        )
        if digest != route.native_text_sha256 or native_characters != route.native_characters:
            raise F0FError("NATIVE_TEXT_MISMATCH")
        body = native_body(text)
        del text
        source.reverify()
        return body
    except F0FError:
        raise
    except Exception:
        raise F0FError("NATIVE_PARSE_FAILED") from None
    finally:
        if handle is not None:
            handle.close()
        if duplicate >= 0:
            os.close(duplicate)


@contextmanager
def _quiet_pypdf() -> Iterator[None]:
    logger = logging.getLogger("pypdf")
    old_disabled = logger.disabled
    old_handlers = list(logger.handlers)
    old_propagate = logger.propagate
    logger.disabled = True
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logger.disabled = old_disabled
        logger.handlers = old_handlers
        logger.propagate = old_propagate


__all__ = ("extract_native_page",)
