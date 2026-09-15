from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

import fitz


MM_TO_PT = 72 / 25.4

PAPER_SIZES = {
    "a4": (210 * MM_TO_PT, 297 * MM_TO_PT),
    "letter": (612.0, 792.0),
}

OUTPUT_NAMES = {
    "print_2up_portrait.pdf",
    "print_2up.pdf",
    "invoice_print.pdf",
}


class PdfImpositionError(RuntimeError):
    """Raised when a PDF cannot be read or imposed."""


@dataclass(frozen=True)
class ImpositionSettings:
    paper: str = "a4"
    orientation: str = "portrait"
    layout: str = "two_vertical"
    margin_mm: float = 5.0
    gap_mm: float = 2.0
    draw_separator: bool = True
    max_sheets: int | None = None


@dataclass(frozen=True)
class SourcePage:
    document: fitz.Document
    page_index: int
    path: Path


def natural_key(path: Path | str) -> list[object]:
    name = Path(path).name.casefold()
    parts = re.split(r"(\d+)", name)
    return [int(part) if part.isdigit() else part for part in parts]


def discover_pdfs(base_dir: Path, output: Path | None = None) -> list[Path]:
    output_resolved = output.resolve() if output else None
    result: list[Path] = []
    seen: set[Path] = set()

    for path in sorted(base_dir.glob("*.pdf"), key=natural_key):
        resolved = path.resolve()
        if resolved in seen:
            continue
        if output_resolved and resolved == output_resolved:
            continue
        if path.name in OUTPUT_NAMES:
            continue
        seen.add(resolved)
        result.append(resolved)

    return result


def resolve_inputs(inputs: Iterable[str], output: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    for item in inputs:
        path = Path(item).expanduser()
        if path.is_dir():
            candidates.extend(path.glob("*.pdf"))
        elif path.is_file() and path.suffix.casefold() == ".pdf":
            candidates.append(path)
        else:
            raise PdfImpositionError(f"不是 PDF 文件或目录: {item}")

    output_resolved = output.resolve() if output else None
    result: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(candidates, key=natural_key):
        resolved = path.resolve()
        if resolved in seen:
            continue
        if output_resolved and resolved == output_resolved:
            continue
        seen.add(resolved)
        result.append(resolved)

    return result


def validate_settings(settings: ImpositionSettings) -> None:
    if settings.paper not in PAPER_SIZES:
        raise PdfImpositionError(f"不支持的纸张大小: {settings.paper}")
    if settings.orientation not in {"portrait", "landscape"}:
        raise PdfImpositionError(f"不支持的纸张方向: {settings.orientation}")
    if settings.layout not in {"single", "two_vertical", "two_horizontal", "four_grid"}:
        raise PdfImpositionError(f"不支持的排版方式: {settings.layout}")
    if settings.margin_mm < 0:
        raise PdfImpositionError("边距必须大于等于 0")
    if settings.gap_mm < 0:
        raise PdfImpositionError("间距必须大于等于 0")
    if settings.max_sheets is not None and settings.max_sheets <= 0:
        raise PdfImpositionError("最大输出页数必须大于 0")


def page_size(settings: ImpositionSettings) -> tuple[float, float]:
    width, height = PAPER_SIZES[settings.paper]
    if settings.orientation == "landscape":
        return height, width
    return width, height


def collect_pages(pdf_paths: Iterable[Path]) -> tuple[list[fitz.Document], list[SourcePage]]:
    docs: list[fitz.Document] = []
    pages: list[SourcePage] = []

    for path in pdf_paths:
        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise PdfImpositionError(f"无法打开 PDF: {path}") from exc

        if doc.needs_pass:
            doc.close()
            raise PdfImpositionError(f"PDF 已加密，需要密码: {path}")

        docs.append(doc)
        for page_index in range(doc.page_count):
            pages.append(SourcePage(doc, page_index, path))

    return docs, pages


def count_pdf_pages(pdf_paths: Iterable[Path]) -> int:
    docs, pages = collect_pages(pdf_paths)
    try:
        return len(pages)
    finally:
        for doc in docs:
            doc.close()


def ensure_printable_slots(slots: list[fitz.Rect]) -> list[fitz.Rect]:
    for slot in slots:
        if slot.width <= 0 or slot.height <= 0:
            raise PdfImpositionError("边距或中间间距过大，版位没有可用打印区域")
    return slots


def slots_for_page(settings: ImpositionSettings) -> list[fitz.Rect]:
    width, height = page_size(settings)
    margin = settings.margin_mm * MM_TO_PT
    gap = settings.gap_mm * MM_TO_PT

    if width <= margin * 2 or height <= margin * 2:
        raise PdfImpositionError("边距过大，页面没有可用打印区域")

    if settings.layout == "single":
        return ensure_printable_slots([fitz.Rect(margin, margin, width - margin, height - margin)])

    if settings.layout == "four_grid":
        mid_x = width / 2
        mid_y = height / 2
        return ensure_printable_slots([
            fitz.Rect(margin, margin, mid_x - gap / 2, mid_y - gap / 2),
            fitz.Rect(mid_x + gap / 2, margin, width - margin, mid_y - gap / 2),
            fitz.Rect(margin, mid_y + gap / 2, mid_x - gap / 2, height - margin),
            fitz.Rect(mid_x + gap / 2, mid_y + gap / 2, width - margin, height - margin),
        ])

    if settings.layout == "two_horizontal":
        half = width / 2
        return ensure_printable_slots([
            fitz.Rect(margin, margin, half - gap / 2, height - margin),
            fitz.Rect(half + gap / 2, margin, width - margin, height - margin),
        ])

    half = height / 2
    return ensure_printable_slots([
        fitz.Rect(margin, margin, width - margin, half - gap / 2),
        fitz.Rect(margin, half + gap / 2, width - margin, height - margin),
    ])


def draw_guides(page: fitz.Page, settings: ImpositionSettings) -> None:
    if not settings.draw_separator or settings.layout == "single":
        return

    width, height = page_size(settings)
    margin = settings.margin_mm * MM_TO_PT

    if settings.layout in {"two_horizontal", "four_grid"}:
        x = width / 2
        page.draw_line(
            fitz.Point(x, margin),
            fitz.Point(x, height - margin),
            color=(0.72, 0.72, 0.72),
            width=0.4,
        )
        if settings.layout == "two_horizontal":
            return

    y = height / 2
    page.draw_line(
        fitz.Point(margin, y),
        fitz.Point(width - margin, y),
        color=(0.72, 0.72, 0.72),
        width=0.4,
    )


def build_imposed_document(pdf_paths: Iterable[Path], settings: ImpositionSettings) -> fitz.Document:
    validate_settings(settings)
    paths = [Path(path).expanduser().resolve() for path in pdf_paths]
    if not paths:
        raise PdfImpositionError("请先添加 PDF 发票")

    docs, source_pages = collect_pages(paths)
    out = fitz.open()
    try:
        if not source_pages:
            raise PdfImpositionError("输入 PDF 没有页面")

        slots = slots_for_page(settings)
        width, height = page_size(settings)
        pages_per_sheet = len(slots)
        source_limit = None
        if settings.max_sheets is not None:
            source_limit = settings.max_sheets * pages_per_sheet

        selected_pages = source_pages[:source_limit]
        for index in range(0, len(selected_pages), pages_per_sheet):
            sheet = out.new_page(width=width, height=height)
            for slot_index, slot in enumerate(slots):
                page_ref_index = index + slot_index
                if page_ref_index >= len(selected_pages):
                    break
                source = selected_pages[page_ref_index]
                sheet.show_pdf_page(
                    slot,
                    source.document,
                    source.page_index,
                    keep_proportion=True,
                    overlay=True,
                )
            draw_guides(sheet, settings)
    except Exception:
        out.close()
        raise
    finally:
        for doc in docs:
            doc.close()

    return out


def save_imposed_pdf(pdf_paths: Iterable[Path], output: Path, settings: ImpositionSettings) -> int:
    output = Path(output).expanduser()
    input_paths = [Path(path).expanduser().resolve() for path in pdf_paths]
    if output.resolve() in input_paths:
        raise PdfImpositionError("输出文件不能覆盖输入发票 PDF")

    doc = build_imposed_document(input_paths, settings)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output, garbage=4, deflate=True)
        return doc.page_count
    finally:
        doc.close()
