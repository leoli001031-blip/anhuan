"""Observed geometry and native-structure location contracts for F0-I."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from .contracts import (
    F0IError,
    canonical_sha256,
    require_positive,
    require_sha256,
)


_BOX_VALUE = re.compile(r"^-?(?:0|[1-9][0-9]{0,8})\.[0-9]{3}$")
_ROTATIONS = frozenset({0, 90, 180, 270})


@dataclass(frozen=True, slots=True)
class GeometryEvidence:
    location_kind: str
    location_status: str
    location_reason_code: str | None
    coordinate_space: str
    reading_order_status: str
    bbox_ppm: tuple[
        tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]
    ] | None
    render_width_px: int | None
    render_height_px: int | None
    page_rotation: int | None
    location_sha256: str

    def __post_init__(self) -> None:
        if self.page_rotation is not None and not _is_rotation(self.page_rotation):
            raise F0IError("GEOMETRY_INVALID")
        require_sha256(self.location_sha256)
        if self.location_status == "AVAILABLE":
            if (
                self.location_kind != "OCR_QUADRILATERAL"
                or self.location_reason_code is not None
                or self.coordinate_space != "TOP_LEFT_PPM"
                or self.reading_order_status != "READING_ORDER_CANDIDATE"
                or self.bbox_ppm is None
                or self.render_width_px is None
                or self.render_height_px is None
            ):
                raise F0IError("GEOMETRY_INVALID")
            require_positive(self.render_width_px)
            require_positive(self.render_height_px)
            _validate_ppm_box(self.bbox_ppm)
            if self.location_sha256 != canonical_sha256(self._identity_payload()):
                raise F0IError("GEOMETRY_INVALID")
            return
        native_unavailable = (
            self.location_status == "UNAVAILABLE"
            and self.location_kind == "NATIVE_TEXT"
            and self.location_reason_code == "NATIVE_LAYOUT_NOT_CAPTURED"
            and self.coordinate_space == "UNAVAILABLE"
            and self.reading_order_status == "UNAVAILABLE"
            and self.bbox_ppm is None
            and self.render_width_px is None
            and self.render_height_px is None
            and _is_rotation(self.page_rotation)
        )
        empty_ocr_unavailable = (
            self.location_status == "UNAVAILABLE"
            and self.location_kind == "OCR_EMPTY_PAGE"
            and self.location_reason_code == "OCR_EMPTY_RESULT"
            and self.coordinate_space == "UNAVAILABLE"
            and self.reading_order_status == "UNAVAILABLE"
            and self.bbox_ppm is None
            and self.render_width_px is None
            and self.render_height_px is None
        )
        if not (native_unavailable or empty_ocr_unavailable):
            raise F0IError("GEOMETRY_INVALID")
        if self.location_sha256 != canonical_sha256(self._identity_payload()):
            raise F0IError("GEOMETRY_INVALID")

    def _identity_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "coordinate_space": self.coordinate_space,
            "location_kind": self.location_kind,
            "page_rotation": self.page_rotation,
            "reading_order_status": self.reading_order_status,
        }
        if self.location_status == "AVAILABLE":
            payload.update(
                {
                    "bbox_ppm": self.bbox_ppm,
                    "render_height_px": self.render_height_px,
                    "render_width_px": self.render_width_px,
                }
            )
        else:
            payload["location_reason_code"] = self.location_reason_code
        return payload

    def to_record(self) -> dict[str, object]:
        return {
            "location_kind": self.location_kind,
            "location_status": self.location_status,
            "location_reason_code": self.location_reason_code,
            "location_sha256": self.location_sha256,
            "coordinate_space": self.coordinate_space,
            "reading_order_status": self.reading_order_status,
            "bbox_ppm": (
                [list(point) for point in self.bbox_ppm]
                if self.bbox_ppm is not None
                else None
            ),
            "render_width_px": self.render_width_px,
            "render_height_px": self.render_height_px,
            "page_rotation": self.page_rotation,
        }


@dataclass(frozen=True, slots=True)
class PageGeometryEvidence:
    media_box: tuple[str, str, str, str]
    crop_box: tuple[str, str, str, str]
    rotation: int
    geometry_sha256: str

    def __post_init__(self) -> None:
        if not _is_rotation(self.rotation):
            raise F0IError("GEOMETRY_INVALID")
        for box in (self.media_box, self.crop_box):
            _validate_page_box(box)
        require_sha256(self.geometry_sha256)
        if self.geometry_sha256 != canonical_sha256(
            {
                "crop_box": self.crop_box,
                "media_box": self.media_box,
                "rotation": self.rotation,
            }
        ):
            raise F0IError("GEOMETRY_INVALID")

    def to_record(self) -> dict[str, object]:
        return {
            "media_box": {
                "left": self.media_box[0],
                "bottom": self.media_box[1],
                "right": self.media_box[2],
                "top": self.media_box[3],
            },
            "crop_box": {
                "left": self.crop_box[0],
                "bottom": self.crop_box[1],
                "right": self.crop_box[2],
                "top": self.crop_box[3],
            },
            "rotation": self.rotation,
            "geometry_sha256": self.geometry_sha256,
        }


@dataclass(frozen=True, slots=True)
class DocxLocation:
    location_kind: str
    structure_ordinal: int
    docx_block_ordinal: int
    docx_paragraph_ordinal: int | None
    docx_table_ordinal: int | None
    docx_row_ordinal: int | None
    docx_cell_ordinal: int | None
    location_status: str
    location_reason_code: str | None
    location_sha256: str

    def __post_init__(self) -> None:
        require_positive(self.structure_ordinal)
        require_positive(self.docx_block_ordinal)
        if self.location_status != "OBSERVED" or self.location_reason_code is not None:
            raise F0IError("STRUCTURE_LOCATION_INVALID")
        if self.location_kind == "DOCX_PARAGRAPH":
            if (
                self.docx_paragraph_ordinal is None
                or any(
                    value is not None
                    for value in (
                        self.docx_table_ordinal,
                        self.docx_row_ordinal,
                        self.docx_cell_ordinal,
                    )
                )
            ):
                raise F0IError("STRUCTURE_LOCATION_INVALID")
            require_positive(self.docx_paragraph_ordinal)
        elif self.location_kind == "DOCX_TABLE_CELL":
            if self.docx_paragraph_ordinal is not None or any(
                value is None
                for value in (
                    self.docx_table_ordinal,
                    self.docx_row_ordinal,
                    self.docx_cell_ordinal,
                )
            ):
                raise F0IError("STRUCTURE_LOCATION_INVALID")
            require_positive(self.docx_table_ordinal)
            require_positive(self.docx_row_ordinal)
            require_positive(self.docx_cell_ordinal)
        else:
            raise F0IError("STRUCTURE_LOCATION_INVALID")
        require_sha256(self.location_sha256)
        if self.location_sha256 != canonical_sha256(self._identity_record()):
            raise F0IError("STRUCTURE_LOCATION_INVALID")

    def _identity_record(self) -> dict[str, object]:
        return {
            "location_kind": self.location_kind,
            "structure_ordinal": self.structure_ordinal,
            "docx_block_ordinal": self.docx_block_ordinal,
            "docx_paragraph_ordinal": self.docx_paragraph_ordinal,
            "docx_table_ordinal": self.docx_table_ordinal,
            "docx_row_ordinal": self.docx_row_ordinal,
            "docx_cell_ordinal": self.docx_cell_ordinal,
        }

    def to_record(self) -> dict[str, object]:
        return {
            **self._identity_record(),
            "location_status": self.location_status,
            "location_reason_code": self.location_reason_code,
            "location_sha256": self.location_sha256,
        }


@dataclass(frozen=True, slots=True)
class XlsxLocation:
    location_kind: str
    structure_ordinal: int
    xlsx_sheet_ordinal: int
    xlsx_row_ordinal: int | None
    xlsx_column_ordinal: int | None
    location_status: str
    location_reason_code: str | None
    location_sha256: str

    def __post_init__(self) -> None:
        require_positive(self.structure_ordinal)
        require_positive(self.xlsx_sheet_ordinal)
        if self.location_status != "OBSERVED" or self.location_reason_code is not None:
            raise F0IError("STRUCTURE_LOCATION_INVALID")
        if self.location_kind == "XLSX_CELL":
            if self.xlsx_row_ordinal is None or self.xlsx_column_ordinal is None:
                raise F0IError("STRUCTURE_LOCATION_INVALID")
            require_positive(self.xlsx_row_ordinal)
            require_positive(self.xlsx_column_ordinal)
        elif self.location_kind == "XLSX_SHEET":
            if self.xlsx_row_ordinal is not None or self.xlsx_column_ordinal is not None:
                raise F0IError("STRUCTURE_LOCATION_INVALID")
        else:
            raise F0IError("STRUCTURE_LOCATION_INVALID")
        require_sha256(self.location_sha256)
        if self.location_sha256 != canonical_sha256(self._identity_record()):
            raise F0IError("STRUCTURE_LOCATION_INVALID")

    def _identity_record(self) -> dict[str, object]:
        return {
            "location_kind": self.location_kind,
            "structure_ordinal": self.structure_ordinal,
            "xlsx_sheet_ordinal": self.xlsx_sheet_ordinal,
            "xlsx_row_ordinal": self.xlsx_row_ordinal,
            "xlsx_column_ordinal": self.xlsx_column_ordinal,
        }

    def to_record(self) -> dict[str, object]:
        return {
            **self._identity_record(),
            "location_status": self.location_status,
            "location_reason_code": self.location_reason_code,
            "location_sha256": self.location_sha256,
        }


def ocr_bbox_to_ppm(
    bbox: object,
    *,
    render_width_px: int,
    render_height_px: int,
    page_rotation: int | None,
) -> GeometryEvidence:
    """Normalize one observed top-left pixel quadrilateral without estimation."""

    width = require_positive(render_width_px)
    height = require_positive(render_height_px)
    if page_rotation is not None and not _is_rotation(page_rotation):
        raise F0IError("GEOMETRY_INVALID")
    try:
        raw_points = tuple(bbox)  # type: ignore[arg-type]
    except TypeError:
        raise F0IError("GEOMETRY_INVALID") from None
    if len(raw_points) != 4:
        raise F0IError("GEOMETRY_INVALID")
    pixel_points: list[tuple[int, int]] = []
    for raw_point in raw_points:
        try:
            point = tuple(raw_point)  # type: ignore[arg-type]
        except TypeError:
            raise F0IError("GEOMETRY_INVALID") from None
        if len(point) != 2:
            raise F0IError("GEOMETRY_INVALID")
        x, y = point
        if (
            isinstance(x, bool)
            or not isinstance(x, int)
            or isinstance(y, bool)
            or not isinstance(y, int)
            or not 0 <= x <= width
            or not 0 <= y <= height
        ):
            raise F0IError("GEOMETRY_INVALID")
        pixel_points.append((x, y))
    if (
        min(point[0] for point in pixel_points)
        >= max(point[0] for point in pixel_points)
        or min(point[1] for point in pixel_points)
        >= max(point[1] for point in pixel_points)
    ):
        raise F0IError("GEOMETRY_INVALID")
    ppm = tuple(
        (
            _rounded_ppm(x, width),
            _rounded_ppm(y, height),
        )
        for x, y in pixel_points
    )
    typed_ppm = (ppm[0], ppm[1], ppm[2], ppm[3])
    location_payload = {
        "bbox_ppm": typed_ppm,
        "coordinate_space": "TOP_LEFT_PPM",
        "location_kind": "OCR_QUADRILATERAL",
        "page_rotation": page_rotation,
        "reading_order_status": "READING_ORDER_CANDIDATE",
        "render_height_px": height,
        "render_width_px": width,
    }
    return GeometryEvidence(
        location_kind="OCR_QUADRILATERAL",
        location_status="AVAILABLE",
        location_reason_code=None,
        coordinate_space="TOP_LEFT_PPM",
        reading_order_status="READING_ORDER_CANDIDATE",
        bbox_ppm=typed_ppm,
        render_width_px=width,
        render_height_px=height,
        page_rotation=page_rotation,
        location_sha256=canonical_sha256(location_payload),
    )


def native_geometry(*, page_rotation: int) -> GeometryEvidence:
    """Return the only permitted geometry state for native text."""

    if not _is_rotation(page_rotation):
        raise F0IError("GEOMETRY_INVALID")
    payload = {
        "coordinate_space": "UNAVAILABLE",
        "location_kind": "NATIVE_TEXT",
        "location_reason_code": "NATIVE_LAYOUT_NOT_CAPTURED",
        "page_rotation": page_rotation,
        "reading_order_status": "UNAVAILABLE",
    }
    return GeometryEvidence(
        location_kind="NATIVE_TEXT",
        location_status="UNAVAILABLE",
        location_reason_code="NATIVE_LAYOUT_NOT_CAPTURED",
        coordinate_space="UNAVAILABLE",
        reading_order_status="UNAVAILABLE",
        bbox_ppm=None,
        render_width_px=None,
        render_height_px=None,
        page_rotation=page_rotation,
        location_sha256=canonical_sha256(payload),
    )


def ocr_geometry_unavailable(*, page_rotation: int | None) -> GeometryEvidence:
    """Represent a real OCR unit that yielded no observed block geometry."""

    if (
        page_rotation is not None and not _is_rotation(page_rotation)
    ):
        raise F0IError("GEOMETRY_INVALID")
    payload = {
        "coordinate_space": "UNAVAILABLE",
        "location_kind": "OCR_EMPTY_PAGE",
        "location_reason_code": "OCR_EMPTY_RESULT",
        "page_rotation": page_rotation,
        "reading_order_status": "UNAVAILABLE",
    }
    return GeometryEvidence(
        location_kind="OCR_EMPTY_PAGE",
        location_status="UNAVAILABLE",
        location_reason_code="OCR_EMPTY_RESULT",
        coordinate_space="UNAVAILABLE",
        reading_order_status="UNAVAILABLE",
        bbox_ppm=None,
        render_width_px=None,
        render_height_px=None,
        page_rotation=page_rotation,
        location_sha256=canonical_sha256(payload),
    )


def page_geometry(
    *, media_box: object, crop_box: object, rotation: int
) -> PageGeometryEvidence:
    media = _box_tuple(media_box)
    crop = _box_tuple(crop_box)
    if not _is_rotation(rotation):
        raise F0IError("GEOMETRY_INVALID")
    payload = {"crop_box": crop, "media_box": media, "rotation": rotation}
    return PageGeometryEvidence(
        media_box=media,
        crop_box=crop,
        rotation=rotation,
        geometry_sha256=canonical_sha256(payload),
    )


def docx_paragraph_location(
    *, structure_ordinal: int, block_ordinal: int, paragraph_ordinal: int
) -> DocxLocation:
    payload = {
        "location_kind": "DOCX_PARAGRAPH",
        "structure_ordinal": structure_ordinal,
        "docx_block_ordinal": block_ordinal,
        "docx_paragraph_ordinal": paragraph_ordinal,
        "docx_table_ordinal": None,
        "docx_row_ordinal": None,
        "docx_cell_ordinal": None,
    }
    return DocxLocation(
        location_kind="DOCX_PARAGRAPH",
        structure_ordinal=structure_ordinal,
        docx_block_ordinal=block_ordinal,
        docx_paragraph_ordinal=paragraph_ordinal,
        docx_table_ordinal=None,
        docx_row_ordinal=None,
        docx_cell_ordinal=None,
        location_status="OBSERVED",
        location_reason_code=None,
        location_sha256=canonical_sha256(payload),
    )


def docx_table_cell_location(
    *,
    structure_ordinal: int,
    block_ordinal: int,
    table_ordinal: int,
    row_ordinal: int,
    cell_ordinal: int,
) -> DocxLocation:
    payload = {
        "location_kind": "DOCX_TABLE_CELL",
        "structure_ordinal": structure_ordinal,
        "docx_block_ordinal": block_ordinal,
        "docx_paragraph_ordinal": None,
        "docx_table_ordinal": table_ordinal,
        "docx_row_ordinal": row_ordinal,
        "docx_cell_ordinal": cell_ordinal,
    }
    return DocxLocation(
        location_kind="DOCX_TABLE_CELL",
        structure_ordinal=structure_ordinal,
        docx_block_ordinal=block_ordinal,
        docx_paragraph_ordinal=None,
        docx_table_ordinal=table_ordinal,
        docx_row_ordinal=row_ordinal,
        docx_cell_ordinal=cell_ordinal,
        location_status="OBSERVED",
        location_reason_code=None,
        location_sha256=canonical_sha256(payload),
    )


def xlsx_sheet_location(*, structure_ordinal: int, sheet_ordinal: int) -> XlsxLocation:
    payload = {
        "location_kind": "XLSX_SHEET",
        "structure_ordinal": structure_ordinal,
        "xlsx_sheet_ordinal": sheet_ordinal,
        "xlsx_row_ordinal": None,
        "xlsx_column_ordinal": None,
    }
    return XlsxLocation(
        location_kind="XLSX_SHEET",
        structure_ordinal=structure_ordinal,
        xlsx_sheet_ordinal=sheet_ordinal,
        xlsx_row_ordinal=None,
        xlsx_column_ordinal=None,
        location_status="OBSERVED",
        location_reason_code=None,
        location_sha256=canonical_sha256(payload),
    )


def xlsx_cell_location(
    *,
    structure_ordinal: int,
    sheet_ordinal: int,
    row_ordinal: int,
    column_ordinal: int,
) -> XlsxLocation:
    payload = {
        "location_kind": "XLSX_CELL",
        "structure_ordinal": structure_ordinal,
        "xlsx_sheet_ordinal": sheet_ordinal,
        "xlsx_row_ordinal": row_ordinal,
        "xlsx_column_ordinal": column_ordinal,
    }
    return XlsxLocation(
        location_kind="XLSX_CELL",
        structure_ordinal=structure_ordinal,
        xlsx_sheet_ordinal=sheet_ordinal,
        xlsx_row_ordinal=row_ordinal,
        xlsx_column_ordinal=column_ordinal,
        location_status="OBSERVED",
        location_reason_code=None,
        location_sha256=canonical_sha256(payload),
    )


def structure_unit_sha256(
    *,
    source_version_sha256: str,
    source_plan_sha256: str,
    structure_anchor_sha256: str,
    unit_kind: str,
    unit_ordinal: int,
) -> str:
    """Identify a native structure unit without inventing an upstream UUID."""

    for value in (
        source_version_sha256,
        source_plan_sha256,
        structure_anchor_sha256,
    ):
        require_sha256(value)
    if unit_kind not in {"DOCX_SECTION", "XLSX_SHEET"}:
        raise F0IError("STRUCTURE_LOCATION_INVALID")
    require_positive(unit_ordinal)
    return canonical_sha256(
        {
            "source_plan_sha256": source_plan_sha256,
            "source_version_sha256": source_version_sha256,
            "structure_anchor_sha256": structure_anchor_sha256,
            "unit_kind": unit_kind,
            "unit_ordinal": unit_ordinal,
        }
    )


def pdf_table_status() -> dict[str, str]:
    """Expose the explicit non-claim required while PDF table parsing is out of scope."""

    return {
        "table_status": "UNRESOLVED",
        "table_reason_code": "PDF_TABLE_MODEL_NOT_IN_SCOPE",
    }


def _rounded_ppm(value: int, extent: int) -> int:
    return (value * 1_000_000 + extent // 2) // extent


def _is_rotation(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value in _ROTATIONS


def _validate_ppm_box(
    value: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]
) -> None:
    if len(value) != 4:
        raise F0IError("GEOMETRY_INVALID")
    for point in value:
        if (
            not isinstance(point, tuple)
            or len(point) != 2
            or any(
                isinstance(coordinate, bool)
                or not isinstance(coordinate, int)
                or not 0 <= coordinate <= 1_000_000
                for coordinate in point
            )
        ):
            raise F0IError("GEOMETRY_INVALID")
    if (
        min(point[0] for point in value) >= max(point[0] for point in value)
        or min(point[1] for point in value) >= max(point[1] for point in value)
    ):
        raise F0IError("GEOMETRY_INVALID")


def _box_tuple(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, dict) or set(value) != {
        "left",
        "bottom",
        "right",
        "top",
    }:
        raise F0IError("GEOMETRY_INVALID")
    result = (
        value["left"],
        value["bottom"],
        value["right"],
        value["top"],
    )
    _validate_page_box(result)
    return result


def _validate_page_box(value: tuple[str, str, str, str]) -> None:
    if not isinstance(value, tuple) or len(value) != 4:
        raise F0IError("GEOMETRY_INVALID")
    try:
        if any(
            not isinstance(item, str) or _BOX_VALUE.fullmatch(item) is None
            for item in value
        ):
            raise F0IError("GEOMETRY_INVALID")
        left, bottom, right, top = (Decimal(item) for item in value)
        if left >= right or bottom >= top:
            raise F0IError("GEOMETRY_INVALID")
    except (InvalidOperation, ValueError):
        raise F0IError("GEOMETRY_INVALID") from None


__all__ = (
    "DocxLocation",
    "GeometryEvidence",
    "PageGeometryEvidence",
    "XlsxLocation",
    "docx_paragraph_location",
    "docx_table_cell_location",
    "native_geometry",
    "ocr_bbox_to_ppm",
    "ocr_geometry_unavailable",
    "page_geometry",
    "pdf_table_status",
    "structure_unit_sha256",
    "xlsx_cell_location",
    "xlsx_sheet_location",
)
