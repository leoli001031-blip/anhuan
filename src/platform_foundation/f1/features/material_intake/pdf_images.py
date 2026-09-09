"""Conservative routing when native text cannot establish visible PDF content."""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.metadata import version
import math

from pypdf.generic import ByteStringObject, NameObject, TextStringObject

from .pdf_renderer import MAX_EDGE, MAX_PIXELS, RENDER_DPI

# These private tables are the Adobe Core14 AFM metrics bundled with the pinned
# parser. Never reuse this proof after a dependency change without review.
try:
    if version("pypdf") != "6.14.2":
        raise ImportError
    from pypdf._codecs import _std_encoding, _win_encoding
    from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
except (ImportError, OSError):
    CORE_FONT_METRICS = {}
    _std_encoding = _win_encoding = ()

_CORE_TEXT_FONTS = frozenset(
    ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
     "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
     "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic")
)
_SIMPLE_FONT_KEYS = frozenset(("/Type", "/Subtype", "/BaseFont", "/Encoding", "/Name"))
_MAX_OPERATIONS = 50_000
_MAX_CHARACTERS = 100_000
_MAX_TEXT_RUNS = 512


def _numbers(values, count: int) -> tuple[float, ...]:
    if len(values) != count or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) for value in values
    ):
        raise ValueError
    return tuple(float(value) for value in values)


@dataclass
class _TextState:
    font: str | None = None
    encoding: str = "/StandardEncoding"
    size: float = 0
    character_spacing: float = 0
    word_spacing: float = 0
    leading: float = 0


def _native_text_is_visible(page, operations) -> bool:
    """Prove a restricted horizontal text stream; unknown constructs fail closed.

    Only unmodified Core14 fonts and printable ASCII have a reliable mapping
    from encoded bytes to known glyphs here. Custom fonts, CMaps and font
    programs require the whole-page renderer, even if pypdf extracts text.
    """
    left, bottom, right, top = _numbers(tuple(page.mediabox), 4)
    width, height = right - left, top - bottom
    if not 1 <= width <= 14400 or not 1 <= height <= 14400:
        return False
    render_scale = min(RENDER_DPI / 72, (MAX_EDGE - 1) / max(width, height),
                       math.sqrt((MAX_PIXELS - 2 * MAX_EDGE) / (width * height)))
    state = _TextState()
    saved = []
    in_text = False
    sx = sy = 1.0
    x = y = line_x = line_y = 0.0
    characters = 0
    painted_runs = []
    current_run = None

    def finish_run() -> None:
        nonlocal current_run
        if current_run is None:
            return
        if len(painted_runs) >= _MAX_TEXT_RUNS:
            raise ValueError
        for previous in painted_runs:
            if (current_run[0] < previous[2] and previous[0] < current_run[2]
                    and current_run[1] < previous[3] and previous[1] < current_run[3]):
                # Repositioned text can overprint existing ink even when both
                # strings individually fit within the page. Keep consecutive
                # Tj / forward TJ segments in one run to allow ordinary text.
                raise ValueError
        painted_runs.append(current_run)
        current_run = None

    def move_line(tx: float, ty: float) -> None:
        nonlocal x, y, line_x, line_y
        finish_run()
        line_x += tx * sx
        line_y += ty * sy
        x, y = line_x, line_y
        if not all(math.isfinite(value) for value in (x, y)):
            raise ValueError

    def show(value) -> None:
        nonlocal x, characters, current_run
        if state.font is None or not isinstance(value, (TextStringObject, ByteStringObject)):
            raise ValueError
        raw = value.original_bytes if isinstance(value, TextStringObject) else bytes(value)
        characters += len(raw)
        if characters > _MAX_CHARACTERS or any(not 32 <= code <= 126 for code in raw):
            raise ValueError
        # Tiny positive values can produce a completely white rendered page.
        if state.size * min(sx, sy) * render_scale < 4:
            raise ValueError
        metrics = CORE_FONT_METRICS[state.font]
        encoding = _std_encoding if state.encoding == "/StandardEncoding" else _win_encoding
        bx0, by0, bx1, by1 = metrics.font_descriptor.bbox
        for code in raw:
            glyph = encoding[code]
            advance = metrics.character_widths[glyph] * state.size / 1000
            if advance <= 0:
                raise ValueError
            if code != 32:
                # The font-wide AFM box is deliberately wider than an individual
                # glyph: if any ink might cross a page edge, request visual OCR.
                box = (x + bx0 * state.size * sx / 1000,
                       y + by0 * state.size * sy / 1000,
                       x + bx1 * state.size * sx / 1000,
                       y + by1 * state.size * sy / 1000)
                if not (left <= box[0] <= box[2] <= right
                        and bottom <= box[1] <= box[3] <= top):
                    raise ValueError
                if current_run is None:
                    current_run = box
                else:
                    current_run = (min(current_run[0], box[0]), min(current_run[1], box[1]),
                                   max(current_run[2], box[2]), max(current_run[3], box[3]))
            x += (advance + state.character_spacing
                  + (state.word_spacing if code == 32 else 0)) * sx
            if not math.isfinite(x):
                raise ValueError

    for args, operator in operations:
        if operator in (b"q", b"Q"):
            if args or in_text:
                return False
            if operator == b"q":
                if len(saved) >= 64:
                    return False
                saved.append(replace(state))
            elif saved:
                state = saved.pop()
            else:
                return False
        elif operator == b"BT":
            if args or in_text:
                return False
            in_text = True
            sx = sy = 1.0
            x = y = line_x = line_y = 0.0
        elif operator == b"ET":
            if args or not in_text:
                return False
            in_text = False
            finish_run()
        elif not in_text:
            return False
        elif operator == b"Tf":
            if len(args) != 2 or not isinstance(args[0], NameObject):
                return False
            size, = _numbers(args[1:], 1)
            font = page["/Resources"]["/Font"][args[0]].get_object()
            name = str(font.get("/BaseFont", ""))[1:]
            encoding = font.get("/Encoding", "/StandardEncoding")
            if (size <= 0 or font.get("/Type", "/Font") != "/Font"
                    or ("/Type" in font and not isinstance(font["/Type"], NameObject))
                    or not isinstance(font.get("/Subtype"), NameObject) or font["/Subtype"] != "/Type1"
                    or not isinstance(font.get("/BaseFont"), NameObject)
                    or ("/Encoding" in font and not isinstance(encoding, NameObject))
                    or not set(font).issubset(_SIMPLE_FONT_KEYS)
                    or name not in _CORE_TEXT_FONTS or name not in CORE_FONT_METRICS
                    or encoding not in ("/StandardEncoding", "/WinAnsiEncoding")):
                return False
            state.font, state.encoding, state.size = name, encoding, size
        elif operator == b"Tm":
            finish_run()
            a, b, c, d, e, f = _numbers(args, 6)
            if a <= 0 or d <= 0 or b != 0 or c != 0:
                return False
            sx, sy, x, y, line_x, line_y = a, d, e, f, e, f
        elif operator in (b"Td", b"TD"):
            tx, ty = _numbers(args, 2)
            if operator == b"TD":
                state.leading = -ty
            move_line(tx, ty)
        elif operator in (b"Tc", b"Tw", b"TL"):
            value, = _numbers(args, 1)
            if value < 0:
                return False
            setattr(state, {b"Tc": "character_spacing", b"Tw": "word_spacing", b"TL": "leading"}[operator], value)
        elif operator in (b"T*", b"'", b'"'):
            if len(args) != {b"T*": 0, b"'": 1, b'"': 3}[operator] or state.leading <= 0:
                return False
            if operator == b'"':
                word_spacing, character_spacing = _numbers(args[:2], 2)
                if min(word_spacing, character_spacing) < 0:
                    return False
                state.word_spacing, state.character_spacing = word_spacing, character_spacing
            move_line(0, -state.leading)
            if args:
                show(args[-1])
        elif operator == b"Tj":
            if len(args) != 1:
                return False
            show(args[0])
        elif operator == b"TJ":
            if len(args) != 1 or not isinstance(args[0], list) or len(args[0]) > _MAX_OPERATIONS:
                return False
            for item in args[0]:
                if isinstance(item, (TextStringObject, ByteStringObject)):
                    show(item)
                else:
                    adjustment, = _numbers((item,), 1)
                    if adjustment > 0:
                        # Positive TJ moves backwards. Even small kerning can
                        # overlap earlier glyphs; the visual engine owns it.
                        return False
                    x -= adjustment * state.size * sx / 1000
                    if not math.isfinite(x):
                        return False
        else:
            # Drawing, clipping, colors, rendering modes, forms and annotations
            # can alter visibility independently of native text coordinates.
            return False
    return not in_text and not saved


def page_requires_visual_ocr(page) -> bool:
    try:
        rotation, = _numbers((page.get("/Rotate", 0),), 1)
        user_unit, = _numbers((page.get("/UserUnit", 1),), 1)
        if page.get("/Annots") or page.get("/Group") or rotation % 360 or user_unit != 1:
            return True
        if tuple(page.cropbox) != tuple(page.mediabox):
            return True
        contents = page.get_contents()
        if contents is None:
            return False
        operations = contents.operations
        if len(operations) > _MAX_OPERATIONS:
            return True
        # Resources alone are not evidence that an image was painted.
        return not _native_text_is_visible(page, operations)
    except Exception:
        return True


__all__ = ("page_requires_visual_ocr",)
