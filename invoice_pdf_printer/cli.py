from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .imposition import (
    ImpositionSettings,
    PdfImpositionError,
    count_pdf_pages,
    discover_pdfs,
    resolve_inputs,
    save_imposed_pdf,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 PDF 发票拼成适合打印的排版 PDF。",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="PDF 文件或目录。省略时读取当前目录所有 *.pdf。",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="print_2up_portrait.pdf",
        help="输出 PDF 路径。默认: print_2up_portrait.pdf",
    )
    parser.add_argument("--paper", choices=["a4", "letter"], default="a4", help="纸张大小。默认: a4")
    parser.add_argument(
        "--orientation",
        choices=["portrait", "landscape"],
        default="portrait",
        help="纸张方向。默认: portrait",
    )
    parser.add_argument(
        "--layout",
        choices=["single", "two-vertical", "two-horizontal", "four-grid"],
        default="two-vertical",
        help="排版方式。默认: two-vertical，上下二合一；four-grid 为一页四张。",
    )
    parser.add_argument("--margin-mm", type=float, default=5.0, help="外边距，单位 mm。默认: 5")
    parser.add_argument("--gap-mm", type=float, default=2.0, help="两个版位之间的间距，单位 mm。默认: 2")
    parser.add_argument("--no-separator", action="store_true", help="不绘制中间裁剪线")
    parser.add_argument("--max-sheets", type=int, default=None, help="最大输出页数")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    output = Path(args.output).expanduser()
    try:
        if args.inputs:
            pdf_paths = resolve_inputs(args.inputs, output)
        else:
            pdf_paths = discover_pdfs(Path.cwd(), output)

        settings = ImpositionSettings(
            paper=args.paper,
            orientation=args.orientation,
            layout=args.layout.replace("-", "_"),
            margin_mm=args.margin_mm,
            gap_mm=args.gap_mm,
            draw_separator=not args.no_separator,
            max_sheets=args.max_sheets,
        )
        input_pages = count_pdf_pages(pdf_paths)
        page_count = save_imposed_pdf(pdf_paths, output, settings)
    except PdfImpositionError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print("输入文件:")
    for path in pdf_paths:
        print(f"  - {path.name}")
    print(f"输入页数: {input_pages}")
    print(f"输出页数: {page_count}")
    print(f"输出文件: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
