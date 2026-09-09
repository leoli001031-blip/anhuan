"""Closed released-native parser contracts; PDF retains its existing pipeline."""
CONTRACTS = {
    "docx": ("docx-native-1", "transitional-main-body-simple-table-1"),
    "xlsx": ("xlsx-native-1", "transitional-visible-cells-no-formulas-1"),
    "jpeg": ("jpeg-ocr-1", "jpeg-oriented-rgb-whole-image-1"),
}
EXTENSIONS = {".docx": "docx", ".xlsx": "xlsx", ".jpg": "jpeg"}


def format_for_key(key: str) -> str | None:
    return EXTENSIONS.get("." + key.rsplit(".", 1)[-1])
