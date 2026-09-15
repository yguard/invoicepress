from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import fitz
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPageLayout,
    QPageSize,
    QPixmap,
    QShortcut,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .imposition import (
    ImpositionSettings,
    PdfImpositionError,
    build_imposed_document,
    discover_pdfs,
    natural_key,
    save_imposed_pdf,
)


APP_TITLE = "InvoicePress 发票打印"


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative_path


def qimage_from_pixmap(pix: fitz.Pixmap) -> QImage:
    if pix.alpha:
        image_format = QImage.Format.Format_RGBA8888
    else:
        image_format = QImage.Format.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, image_format).copy()


def render_pdf_page(page: fitz.Page, scale: float) -> QPixmap:
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return QPixmap.fromImage(qimage_from_pixmap(pix))


def pdf_page_count(path: Path) -> int:
    doc = fitz.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def thumbnail_for_pdf(path: Path, size: QSize) -> QPixmap:
    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            return QPixmap()
        page = doc[0]
        scale = min(size.width() / page.rect.width, size.height() / page.rect.height)
        pixmap = render_pdf_page(page, max(scale * 8.0, 1.5))
        return pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    finally:
        doc.close()


def fit_rect(source_width: int, source_height: int, bounds: QRectF) -> QRectF:
    if source_width <= 0 or source_height <= 0:
        return bounds
    scale = min(bounds.width() / source_width, bounds.height() / source_height)
    width = source_width * scale
    height = source_height * scale
    x = bounds.x() + (bounds.width() - width) / 2
    y = bounds.y() + (bounds.height() - height) / 2
    return QRectF(x, y, width, height)


class PreviewLabel(QLabel):
    def __init__(self) -> None:
        super().__init__("请添加 PDF 发票")
        self._source: QPixmap | None = None
        self._fit_to_window = True
        self._zoom_percent = 100
        self._viewport_size = QSize(640, 720)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 420)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setObjectName("PreviewLabel")

    def set_viewport_size(self, size: QSize) -> None:
        self._viewport_size = size
        self._update_scaled_pixmap()

    def set_source_pixmap(self, pixmap: QPixmap | None) -> None:
        self._source = pixmap
        if pixmap is None or pixmap.isNull():
            self.setText("暂无预览")
            self.setPixmap(QPixmap())
            return
        self.setText("")
        self._update_scaled_pixmap()

    def set_fit_to_window(self, enabled: bool) -> None:
        self._fit_to_window = enabled
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_scaled_pixmap()

    def set_zoom_percent(self, value: int) -> None:
        self._zoom_percent = max(70, min(240, value))
        self._fit_to_window = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if self._source is None or self._source.isNull():
            return
        base_width = max(320, self._viewport_size.width() - 36)
        if self._fit_to_window:
            target_width = base_width
        else:
            target_width = int(base_width * self._zoom_percent / 100)

        target_width = max(240, target_width)
        target_height = max(1, int(self._source.height() * target_width / self._source.width()))
        pixmap = self._source.scaled(
            QSize(target_width, target_height),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pixmap)
        self.resize(pixmap.size())


class PreviewScrollArea(QScrollArea):
    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        widget = self.widget()
        if isinstance(widget, PreviewLabel):
            widget.set_viewport_size(self.viewport().size())
            return


class InvoicePrinterWindow(QMainWindow):
    def __init__(self, start_dir: Path | None = None) -> None:
        super().__init__()
        self.start_dir = (start_dir or Path.cwd()).resolve()
        self.invoices: list[Path] = []
        self.preview_doc: fitz.Document | None = None
        self.current_sheet = 0
        self.zoom_percent = 100
        self.fit_preview = True
        self.page_count_cache: dict[Path, int] = {}
        self.thumbnail_cache: dict[tuple[Path, int, int], QPixmap] = {}

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self.update_preview)

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1160, 720)
        self.resize(1360, 820)
        self._build_ui()
        self._apply_style()
        self._setup_shortcuts()
        self.statusBar().showMessage("准备就绪")
        self.load_default_pdfs()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.preview_doc is not None:
            self.preview_doc.close()
            self.preview_doc = None
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(APP_TITLE)
        title.setObjectName("Title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.clicked.connect(self.open_output_dir)
        title_row.addWidget(self.open_output_button)
        main_layout.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 620, 420])
        main_layout.addWidget(splitter, 1)

        self.setCentralWidget(root)

    def configure_combo(self, combo: QComboBox, popup_width: int, max_visible_items: int = 8) -> None:
        view = QListView()
        view.setObjectName("ComboPopup")
        view.setSpacing(2)
        combo.setView(view)
        combo.setMaxVisibleItems(max_visible_items)
        combo.setMinimumHeight(36)
        combo.setMinimumWidth(0)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.view().setMinimumWidth(popup_width)

    def announce_combo_choice(self, label: str, combo: QComboBox) -> None:
        self.statusBar().showMessage(f"{label}：{combo.currentText()}", 2500)

    def _setup_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+O", self.add_invoices),
            ("Ctrl+Shift+O", self.add_invoice_directory),
            ("Delete", self.remove_selected_invoice),
            ("Backspace", self.remove_selected_invoice),
            ("Ctrl+R", self.reload_current_directory),
            ("Ctrl+S", self.export_pdf),
            ("Ctrl+P", self.print_pdf),
            ("+", lambda: self.change_zoom(10)),
            ("=", lambda: self.change_zoom(10)),
            ("-", lambda: self.change_zoom(-10)),
            ("0", self.fit_preview_to_window),
        ]
        for sequence, handler in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("SidePanel")
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("发票列表")
        header.setObjectName("PanelHeader")
        header_row.addWidget(header)
        header_row.addStretch(1)
        self.invoice_count_label = QLabel("0 张")
        self.invoice_count_label.setObjectName("MutedLabel")
        header_row.addWidget(self.invoice_count_label)
        layout.addLayout(header_row)

        self.invoice_list = QListWidget()
        self.invoice_list.setIconSize(QSize(148, 104))
        self.invoice_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.invoice_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.invoice_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.invoice_list.setAlternatingRowColors(True)
        self.invoice_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.invoice_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.invoice_list.setWordWrap(True)
        self.invoice_list.setSpacing(6)
        self.invoice_list.currentRowChanged.connect(self.invoice_selection_changed)
        self.invoice_list.itemDoubleClicked.connect(lambda _item: self.open_selected_invoice())
        self.invoice_list.model().rowsMoved.connect(lambda *_args: self.sync_invoice_order_from_list())
        layout.addWidget(self.invoice_list, 1)

        button_grid = QGridLayout()
        button_grid.setSpacing(8)
        self.add_button = QPushButton("添加文件")
        self.add_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.add_button.setToolTip("添加一个或多个 PDF 文件（Ctrl+O）")
        self.add_button.clicked.connect(self.add_invoices)
        self.add_dir_button = QPushButton("添加目录")
        self.add_dir_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.add_dir_button.setToolTip("添加目录中的 PDF 文件（Ctrl+Shift+O）")
        self.add_dir_button.clicked.connect(self.add_invoice_directory)
        self.remove_button = QPushButton("移除选中")
        self.remove_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.remove_button.setToolTip("从列表移除选中的 PDF，不删除原文件（Delete）")
        self.remove_button.clicked.connect(self.remove_selected_invoice)
        self.sort_button = QPushButton("按序号排序")
        self.sort_button.setToolTip("按文件名中的数字自然排序")
        self.sort_button.clicked.connect(self.sort_invoices)
        self.scan_button = QPushButton("重新扫描")
        self.scan_button.setToolTip("重新读取当前目录 PDF（Ctrl+R）")
        self.scan_button.clicked.connect(self.reload_current_directory)
        self.up_button = QPushButton("上移")
        self.up_button.clicked.connect(lambda: self.move_selected_invoice(-1))
        self.down_button = QPushButton("下移")
        self.down_button.clicked.connect(lambda: self.move_selected_invoice(1))
        self.open_selected_button = QPushButton("打开")
        self.open_selected_button.clicked.connect(self.open_selected_invoice)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_invoices)
        button_grid.addWidget(self.add_button, 0, 0)
        button_grid.addWidget(self.add_dir_button, 0, 1)
        button_grid.addWidget(self.remove_button, 1, 0)
        button_grid.addWidget(self.open_selected_button, 1, 1)
        button_grid.addWidget(self.up_button, 2, 0)
        button_grid.addWidget(self.down_button, 2, 1)
        button_grid.addWidget(self.sort_button, 3, 0)
        button_grid.addWidget(self.scan_button, 3, 1)
        button_grid.addWidget(self.clear_button, 4, 0, 1, 2)
        layout.addLayout(button_grid)

        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("PreviewPanel")
        panel.setMinimumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.preview_scroll = PreviewScrollArea()
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_scroll.setObjectName("PreviewScroll")
        self.preview_label = PreviewLabel()
        self.preview_scroll.setWidget(self.preview_label)
        layout.addWidget(self.preview_scroll, 1)

        nav = QHBoxLayout()
        nav.addStretch(1)
        self.prev_button = QToolButton()
        self.prev_button.setText("‹")
        self.prev_button.clicked.connect(lambda: self.change_sheet(-1))
        nav.addWidget(self.prev_button)
        self.page_label = QLabel("0/0")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(64)
        nav.addWidget(self.page_label)
        self.next_button = QToolButton()
        self.next_button.setText("›")
        self.next_button.clicked.connect(lambda: self.change_sheet(1))
        nav.addWidget(self.next_button)
        nav.addSpacing(16)
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")
        self.zoom_out_button.clicked.connect(lambda: self.change_zoom(-10))
        nav.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel("宽度")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.zoom_label)
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")
        self.zoom_in_button.clicked.connect(lambda: self.change_zoom(10))
        nav.addWidget(self.zoom_in_button)
        self.fit_button = QToolButton()
        self.fit_button.setText("宽度")
        self.fit_button.setCheckable(True)
        self.fit_button.setChecked(True)
        self.fit_button.clicked.connect(lambda _checked: self.fit_preview_to_window())
        nav.addWidget(self.fit_button)
        nav.addStretch(1)
        layout.addLayout(nav)

        return panel

    def _build_settings_panel(self) -> QWidget:
        outer = QWidget()
        outer.setObjectName("SettingsOuter")
        outer.setMinimumWidth(400)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        panel = QWidget()
        panel.setObjectName("SettingsPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        def add_setting_row(label_text: str, control: QWidget) -> None:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            label = QLabel(label_text)
            label.setObjectName("FieldLabel")
            label.setMinimumWidth(72)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(label)
            row_layout.addWidget(control, 1)
            layout.addWidget(row_widget)

        settings_title = QLabel("打印设置")
        settings_title.setObjectName("PanelHeader")
        layout.addWidget(settings_title)

        self.preset_combo = QComboBox()
        self.configure_combo(self.preset_combo, 330)
        self.preset_combo.setMinimumContentsLength(12)
        self.preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preset_combo.setToolTip("选择预设会同时更新纸张、方向、排版方式和版面参数")
        self.preset_combo.addItem(
            "A4 纵向：上下二合一（常用）",
            ("a4", "portrait", "two_vertical", 5, 2, True),
        )
        self.preset_combo.addItem(
            "A4 纵向：四宫格",
            ("a4", "portrait", "four_grid", 5, 2, True),
        )
        self.preset_combo.addItem(
            "A4 纵向：单页预览",
            ("a4", "portrait", "single", 5, 0, False),
        )
        self.preset_combo.addItem(
            "A4 横向：左右二合一",
            ("a4", "landscape", "two_horizontal", 5, 2, True),
        )
        self.preset_combo.addItem(
            "Letter 纵向：上下二合一",
            ("letter", "portrait", "two_vertical", 5, 2, True),
        )
        self.preset_combo.addItem("自定义设置", "custom")
        preset_tips = [
            "常用打印方式：A4 纵向，每张纸上下放两张票据",
            "A4 纵向一页放四张，适合票据较小且需要节省纸张",
            "检查单张票据比例或需要每页一张时使用",
            "横向纸张，左右各放一张，适合横版内容",
            "Letter 纸张纵向上下二合一",
            "当前参数不完全匹配任一预设",
        ]
        for index, tip in enumerate(preset_tips):
            self.preset_combo.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.preset_combo.currentIndexChanged.connect(lambda _index: self.apply_selected_preset())
        add_setting_row("常用预设", self.preset_combo)

        self.printer_combo = QComboBox()
        self.configure_combo(self.printer_combo, 380, max_visible_items=10)
        self.printer_combo.setToolTip("可直接选择打印机，也可以保留系统打印对话框，在打印时再选择")
        self.printer_combo.setMinimumContentsLength(10)
        self.printer_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.printer_combo.currentIndexChanged.connect(
            lambda _index: self.announce_combo_choice("打印机", self.printer_combo)
        )
        self._load_printers()
        self.refresh_printers_button = QToolButton()
        self.refresh_printers_button.setText("刷新")
        self.refresh_printers_button.setFixedWidth(64)
        self.refresh_printers_button.setToolTip("重新读取系统打印机列表")
        self.refresh_printers_button.clicked.connect(self.refresh_printers)
        printer_control = QWidget()
        printer_layout = QHBoxLayout(printer_control)
        printer_layout.setContentsMargins(0, 0, 0, 0)
        printer_layout.setSpacing(8)
        printer_layout.addWidget(self.printer_combo, 1)
        printer_layout.addWidget(self.refresh_printers_button, 0)
        add_setting_row("打印机", printer_control)

        self.copy_spin = QSpinBox()
        self.copy_spin.setRange(1, 99)
        self.copy_spin.setValue(1)
        self.copy_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        add_setting_row("打印份数", self.copy_spin)

        self.paper_combo = QComboBox()
        self.configure_combo(self.paper_combo, 240)
        self.paper_combo.setToolTip("选择输出 PDF 和打印任务使用的纸张尺寸")
        self.paper_combo.addItem("A4（210 × 297 mm）", "a4")
        self.paper_combo.addItem("Letter（8.5 × 11 in）", "letter")
        self.paper_combo.setItemData(0, "国内常用 A4 纸张", Qt.ItemDataRole.ToolTipRole)
        self.paper_combo.setItemData(1, "北美常用 Letter 纸张", Qt.ItemDataRole.ToolTipRole)
        self.paper_combo.setMinimumContentsLength(12)
        self.paper_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.paper_combo.currentIndexChanged.connect(lambda _index: self.paper_changed())
        add_setting_row("纸张大小", self.paper_combo)

        layout_title = QLabel("打印排版")
        layout_title.setObjectName("PanelHeader")
        layout.addWidget(layout_title)

        orientation_control = QWidget()
        orientation_row = QHBoxLayout(orientation_control)
        orientation_row.setContentsMargins(0, 0, 0, 0)
        orientation_row.setSpacing(16)
        self.portrait_radio = QRadioButton("纵向")
        self.landscape_radio = QRadioButton("横向")
        self.portrait_radio.setChecked(True)
        self.portrait_radio.toggled.connect(lambda checked: self.settings_control_changed() if checked else None)
        self.landscape_radio.toggled.connect(lambda checked: self.settings_control_changed() if checked else None)
        orientation_row.addWidget(self.portrait_radio)
        orientation_row.addWidget(self.landscape_radio)
        orientation_row.addStretch(1)
        add_setting_row("纸张方向", orientation_control)

        layout.addWidget(QLabel("排版方式"))
        self.layout_group = QButtonGroup(self)
        self.layout_group.setExclusive(True)
        layout_grid = QGridLayout()
        layout_grid.setHorizontalSpacing(8)
        layout_grid.setVerticalSpacing(8)
        layout_grid.setColumnStretch(0, 1)
        layout_grid.setColumnStretch(1, 1)
        for index, (text, key) in enumerate([
            ("单页\n每页 1 张", "single"),
            ("上下\n每页 2 张", "two_vertical"),
            ("左右\n每页 2 张", "two_horizontal"),
            ("四宫格\n每页 4 张", "four_grid"),
        ]):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setProperty("layout", key)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumSize(84, 64)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setObjectName("LayoutButton")
            if key == "two_vertical":
                button.setChecked(True)
            self.layout_group.addButton(button)
            layout_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(layout_grid)
        self.layout_group.buttonClicked.connect(lambda _button: self.settings_control_changed())

        self.separator_check = QCheckBox("添加裁剪线")
        self.separator_check.setChecked(True)
        self.separator_check.toggled.connect(lambda _checked: self.settings_control_changed())
        layout.addWidget(self.separator_check)

        spacing_box = QGroupBox("版面参数")
        spacing_layout = QGridLayout(spacing_box)
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 50)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setValue(5)
        self.margin_spin.valueChanged.connect(lambda _value: self.settings_control_changed())
        self.margin_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 50)
        self.gap_spin.setSuffix(" mm")
        self.gap_spin.setValue(2)
        self.gap_spin.valueChanged.connect(lambda _value: self.settings_control_changed())
        self.gap_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        spacing_layout.addWidget(QLabel("外边距"), 0, 0)
        spacing_layout.addWidget(self.margin_spin, 0, 1)
        spacing_layout.addWidget(QLabel("中间间距"), 1, 0)
        spacing_layout.addWidget(self.gap_spin, 1, 1)
        spacing_layout.setColumnStretch(1, 1)
        layout.addWidget(spacing_box)

        output_box = QGroupBox("输出")
        output_layout = QVBoxLayout(output_box)
        output_layout.setSpacing(8)
        self.output_edit = QLineEdit(str(self.start_dir / "print_2up_portrait.pdf"))
        self.output_edit.textChanged.connect(lambda _text: self.update_action_states())
        self.browse_output_button = QPushButton("选择位置")
        self.browse_output_button.clicked.connect(self.choose_output_path)
        self.open_output_pdf_button = QPushButton("打开PDF")
        self.open_output_pdf_button.clicked.connect(self.open_output_file)
        self.open_output_dir_button = QPushButton("打开目录")
        self.open_output_dir_button.clicked.connect(self.open_output_dir)
        output_buttons = QHBoxLayout()
        output_buttons.setSpacing(8)
        output_buttons.addWidget(self.browse_output_button)
        output_buttons.addWidget(self.open_output_pdf_button)
        output_buttons.addWidget(self.open_output_dir_button)
        output_layout.addWidget(QLabel("PDF 输出文件"))
        output_layout.addWidget(self.output_edit)
        output_layout.addLayout(output_buttons)
        layout.addWidget(output_box)

        layout.addStretch(1)

        scroll.setWidget(panel)
        outer_layout.addWidget(scroll, 1)

        footer = QWidget()
        footer.setObjectName("SettingsFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(12, 8, 12, 12)
        footer_layout.setSpacing(8)

        self.summary_line = QLabel("共 0 张票据，输出 0 页")
        self.summary_line.setObjectName("Summary")
        self.summary_line.setWordWrap(True)
        footer_layout.addWidget(self.summary_line)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.export_button = QPushButton("生成PDF")
        self.export_button.setMinimumHeight(42)
        self.export_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.export_button.setToolTip("生成输出 PDF（Ctrl+S）")
        self.export_button.clicked.connect(self.export_pdf)
        self.print_button = QPushButton("开始打印")
        self.print_button.setMinimumHeight(42)
        self.print_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.print_button.setObjectName("PrimaryButton")
        self.print_button.setToolTip("打开系统打印对话框（Ctrl+P）")
        self.print_button.clicked.connect(self.print_pdf)
        action_row.addWidget(self.export_button)
        action_row.addWidget(self.print_button)
        footer_layout.addLayout(action_row)

        outer_layout.addWidget(footer, 0)
        return outer

    def _load_printers(self) -> None:
        previous = str(self.printer_combo.currentData() or "") if self.printer_combo.count() else ""
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("系统打印对话框（打印时选择）", "")
        self.printer_combo.setItemData(
            0,
            "打印时打开系统打印对话框，适合临时换打印机或调整高级打印选项",
            Qt.ItemDataRole.ToolTipRole,
        )

        default_name = ""
        default_printer = QPrinterInfo.defaultPrinter()
        if not default_printer.isNull():
            default_name = default_printer.printerName()

        added: set[str] = set()
        for printer in QPrinterInfo.availablePrinters():
            name = printer.printerName()
            if not name or name in added:
                continue
            added.add(name)
            label = f"默认：{name}" if name == default_name else name
            self.printer_combo.addItem(label, name)
            index = self.printer_combo.count() - 1
            role = "默认打印机" if name == default_name else "可用打印机"
            self.printer_combo.setItemData(index, f"{role}：{name}", Qt.ItemDataRole.ToolTipRole)

        selected = previous or default_name
        if selected:
            index = self.printer_combo.findData(selected)
            if index >= 0:
                self.printer_combo.setCurrentIndex(index)
        self.printer_combo.blockSignals(False)

    def refresh_printers(self) -> None:
        self._load_printers()
        self.statusBar().showMessage("已刷新打印机列表", 2500)

    def paper_changed(self) -> None:
        self.settings_control_changed()
        self.announce_combo_choice("纸张", self.paper_combo)

    def settings_signature(self) -> tuple[str, str, str, int, int, bool]:
        return (
            str(self.paper_combo.currentData()),
            "portrait" if self.portrait_radio.isChecked() else "landscape",
            self.current_layout(),
            int(self.margin_spin.value()),
            int(self.gap_spin.value()),
            self.separator_check.isChecked(),
        )

    def sync_preset_to_settings(self) -> None:
        signature = self.settings_signature()
        target_index = -1
        custom_index = -1
        for index in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(index)
            if data == "custom":
                custom_index = index
            elif data == signature:
                target_index = index
                break

        if target_index < 0:
            target_index = custom_index
        if target_index >= 0 and self.preset_combo.currentIndex() != target_index:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(target_index)
            self.preset_combo.blockSignals(False)

    def settings_control_changed(self) -> None:
        self.sync_preset_to_settings()
        self.schedule_preview()

    def set_current_layout_choice(self, layout_key: str) -> None:
        for button in self.layout_group.buttons():
            if button.property("layout") == layout_key:
                button.setChecked(True)
                return

    def apply_selected_preset(self) -> None:
        data = self.preset_combo.currentData()
        if not data or data == "custom":
            return

        paper, orientation, layout_key, margin_mm, gap_mm, draw_separator = data
        controls = [
            self.paper_combo,
            self.portrait_radio,
            self.landscape_radio,
            self.margin_spin,
            self.gap_spin,
            self.separator_check,
        ]
        for control in controls:
            control.blockSignals(True)
        self.layout_group.blockSignals(True)

        paper_index = self.paper_combo.findData(paper)
        if paper_index >= 0:
            self.paper_combo.setCurrentIndex(paper_index)
        self.portrait_radio.setChecked(orientation == "portrait")
        self.landscape_radio.setChecked(orientation == "landscape")
        self.set_current_layout_choice(str(layout_key))
        self.margin_spin.setValue(int(margin_mm))
        self.gap_spin.setValue(int(gap_mm))
        self.separator_check.setChecked(bool(draw_separator))

        self.layout_group.blockSignals(False)
        for control in controls:
            control.blockSignals(False)

        self.schedule_preview()
        self.statusBar().showMessage(f"已应用预设：{self.preset_combo.currentText()}", 2500)

    def _apply_style(self) -> None:
        arrow_path = resource_path("invoice_pdf_printer/assets/chevron_down.svg").as_posix()
        stylesheet = (
            """
            QMainWindow {
                background: #f3f6f8;
                color: #172033;
                font-size: 14px;
            }
            QWidget {
                color: #172033;
                font-size: 14px;
            }
            QWidget#AppRoot {
                background: #f3f6f8;
            }
            QWidget#SidePanel {
                background: #ffffff;
                border: 1px solid #d6dee8;
                border-radius: 8px;
            }
            QWidget#PreviewPanel {
                background: #e8eef5;
                border: 1px solid #d1dbe8;
                border-radius: 8px;
            }
            QWidget#SettingsPanel {
                background: #ffffff;
                border: 1px solid #d6dee8;
                border-radius: 8px;
            }
            QScrollArea#SettingsScroll {
                background: transparent;
                border: none;
            }
            QWidget#SettingsFooter {
                background: #ffffff;
                border: 1px solid #d6dee8;
                border-radius: 8px;
            }
            QLabel#Title {
                font-size: 18px;
                font-weight: 600;
                color: #111827;
            }
            QLabel#PanelHeader {
                font-size: 15px;
                font-weight: 600;
                padding: 2px 0;
                color: #111827;
            }
            QLabel#FieldLabel {
                color: #475569;
                font-weight: 500;
            }
            QLabel#MutedLabel {
                color: #64748b;
                font-size: 12px;
            }
            QListWidget {
                background: #fbfcfe;
                color: #172033;
                border: 1px solid #d6dee8;
                border-radius: 8px;
                outline: none;
            }
            QListWidget::item:alternate {
                background: #f7f9fc;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
            }
            QListWidget::item:hover {
                background: #eef5ff;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #1e3a8a;
                border: 1px solid #2563eb;
            }
            QScrollArea#PreviewScroll {
                background: #dfe8f2;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            QLabel#PreviewLabel {
                background: #dfe8f2;
                border: none;
            }
            QLineEdit, QComboBox, QSpinBox {
                min-height: 34px;
                padding: 3px 10px;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                color: #172033;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                background: #fbfcfe;
                border-color: #94a3b8;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                background: #ffffff;
                border: 2px solid #2563eb;
                padding: 2px 9px;
            }
            QComboBox {
                padding-right: 34px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #d6dee8;
                background: #eef5ff;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox::drop-down:hover {
                background: #dbeafe;
            }
            QComboBox::down-arrow {
                image: url(__COMBO_ARROW__);
                width: 14px;
                height: 14px;
            }
            QListView#ComboPopup {
                background: #ffffff;
                color: #172033;
                border: 1px solid #94a3b8;
                border-radius: 8px;
                padding: 6px;
                outline: none;
                selection-background-color: #dbeafe;
                selection-color: #1e3a8a;
            }
            QListView#ComboPopup::item {
                min-height: 30px;
                padding: 7px 10px;
                border-radius: 6px;
            }
            QListView#ComboPopup::item:hover {
                background: #eef5ff;
            }
            QListView#ComboPopup::item:selected {
                background: #dbeafe;
                color: #1e3a8a;
            }
            QPushButton, QToolButton {
                min-height: 32px;
                padding: 4px 10px;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                color: #172033;
            }
            QPushButton:hover, QToolButton:hover {
                background: #eef5ff;
                border-color: #2563eb;
            }
            QToolButton#LayoutButton {
                background: #ffffff;
                color: #172033;
                border-color: #cbd5e1;
            }
            QToolButton#LayoutButton:checked {
                background: #dbeafe;
                border: 2px solid #2563eb;
                color: #1e40af;
            }
            QPushButton#PrimaryButton {
                background: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
                font-weight: 600;
                min-height: 34px;
            }
            QPushButton#PrimaryButton:hover {
                background: #1d4ed8;
                border-color: #1d4ed8;
            }
            QPushButton:disabled, QToolButton:disabled {
                color: #94a3b8;
                background: #eef2f7;
                border-color: #d8e0ea;
            }
            QLabel#Summary {
                font-weight: 600;
                color: #172033;
            }
            QGroupBox {
                background: #fbfcfe;
                border: 1px solid #d6dee8;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 10px;
                font-weight: 600;
                color: #172033;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background: #ffffff;
            }
            QStatusBar {
                background: #e8eef5;
                color: #334155;
                border-top: 1px solid #d6dee8;
            }
            QSplitter::handle {
                background: #d6dee8;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 11px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #94a3b8;
                border-radius: 5px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: #2563eb;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 11px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: #94a3b8;
                border-radius: 5px;
                min-width: 28px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #2563eb;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
                background: transparent;
            }
            """
        )
        self.setStyleSheet(stylesheet.replace("__COMBO_ARROW__", arrow_path))

    def load_default_pdfs(self) -> None:
        self.invoices = discover_pdfs(self.start_dir, Path(self.output_edit.text()))
        self.refresh_invoice_list()
        self.schedule_preview()
        self.statusBar().showMessage(f"已加载 {len(self.invoices)} 个 PDF", 2500)

    def get_page_count(self, path: Path) -> int:
        path = path.resolve()
        if path not in self.page_count_cache:
            self.page_count_cache[path] = pdf_page_count(path)
        return self.page_count_cache[path]

    def get_thumbnail(self, path: Path, size: QSize) -> QPixmap:
        path = path.resolve()
        key = (path, size.width(), size.height())
        if key not in self.thumbnail_cache:
            self.thumbnail_cache[key] = thumbnail_for_pdf(path, size)
        return self.thumbnail_cache[key]

    def add_invoices(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "添加 PDF 发票",
            str(self.start_dir),
            "PDF 文件 (*.pdf)",
        )
        if not files:
            return

        added = self.add_invoice_paths(Path(filename) for filename in files)
        self.statusBar().showMessage(f"已添加 {added} 个 PDF", 2500)

    def add_invoice_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择 PDF 目录",
            str(self.start_dir),
        )
        if not directory:
            return

        added = self.add_invoice_paths(Path(directory).glob("*.pdf"))
        self.statusBar().showMessage(f"已从目录添加 {added} 个 PDF", 2500)

    def add_invoice_paths(self, paths) -> int:  # type: ignore[no-untyped-def]
        existing = {path.resolve() for path in self.invoices}
        output_path = Path(self.output_edit.text()).expanduser().resolve()
        added = 0
        for item in paths:
            path = Path(item).expanduser().resolve()
            if path.suffix.casefold() == ".pdf" and path not in existing and path != output_path:
                self.invoices.append(path)
                existing.add(path)
                added += 1
        self.sort_invoices()
        return added

    def reload_current_directory(self) -> None:
        self.load_default_pdfs()

    def remove_selected_invoice(self) -> None:
        row = self.invoice_list.currentRow()
        if row < 0 or row >= len(self.invoices):
            return
        removed = self.invoices[row].name
        del self.invoices[row]
        self.refresh_invoice_list(selected_row=min(row, len(self.invoices) - 1))
        self.schedule_preview()
        self.statusBar().showMessage(f"已移除：{removed}", 2500)

    def move_selected_invoice(self, direction: int) -> None:
        row = self.invoice_list.currentRow()
        new_row = row + direction
        if row < 0 or new_row < 0 or new_row >= len(self.invoices):
            return
        self.invoices[row], self.invoices[new_row] = self.invoices[new_row], self.invoices[row]
        self.refresh_invoice_list(selected_row=new_row)
        self.schedule_preview()
        self.statusBar().showMessage("已调整顺序", 1500)

    def sort_invoices(self) -> None:
        self.invoices = sorted(self.invoices, key=natural_key)
        self.refresh_invoice_list()
        self.schedule_preview()
        self.statusBar().showMessage("已按文件名序号排序", 2000)

    def clear_invoices(self) -> None:
        if not self.invoices:
            return
        self.invoices = []
        self.refresh_invoice_list()
        self.schedule_preview()
        self.statusBar().showMessage("已清空发票列表", 2000)

    def refresh_invoice_list(self, selected_row: int | None = None) -> None:
        self.invoice_list.clear()
        thumb_size = self.invoice_list.iconSize()
        for index, path in enumerate(self.invoices, start=1):
            try:
                page_count = self.get_page_count(path)
                icon_pixmap = self.get_thumbnail(path, thumb_size)
                detail = f"{index}. {path.name}\n{page_count} 页"
            except Exception:
                icon_pixmap = QPixmap()
                detail = f"{index}. {path.name}\n无法读取"
            item = QListWidgetItem(detail)
            item.setSizeHint(QSize(0, 112))
            item.setToolTip(str(path))
            if not icon_pixmap.isNull():
                item.setIcon(QIcon(icon_pixmap))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.invoice_list.addItem(item)

        if self.invoices:
            row = selected_row if selected_row is not None else min(self.invoice_list.currentRow(), len(self.invoices) - 1)
            if row < 0:
                row = 0
            self.invoice_list.setCurrentRow(row)
        self.invoice_count_label.setText(f"{len(self.invoices)} 张")
        self.update_action_states()

    def sync_invoice_order_from_list(self) -> None:
        ordered: list[Path] = []
        for row in range(self.invoice_list.count()):
            item = self.invoice_list.item(row)
            path_text = item.data(Qt.ItemDataRole.UserRole)
            if path_text:
                ordered.append(Path(str(path_text)))
        if ordered and ordered != self.invoices:
            self.invoices = ordered
            self.schedule_preview()
            self.statusBar().showMessage("已按拖拽结果更新顺序", 2000)

    def invoice_selection_changed(self, row: int) -> None:
        self.update_action_states()
        if row < 0:
            return
        layout_pages = {"single": 1, "four_grid": 4}
        pages_per_sheet = layout_pages.get(self.current_layout(), 2)
        page_offset = 0
        for index, path in enumerate(self.invoices):
            if index == row:
                break
            try:
                page_offset += self.get_page_count(path)
            except Exception:
                pass
        self.current_sheet = max(0, page_offset // pages_per_sheet)
        self.render_current_sheet()

    def open_selected_invoice(self) -> None:
        row = self.invoice_list.currentRow()
        if row < 0 or row >= len(self.invoices):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.invoices[row])))

    def current_layout(self) -> str:
        button = self.layout_group.checkedButton()
        if button is None:
            return "two_vertical"
        return str(button.property("layout"))

    def current_settings(self) -> ImpositionSettings:
        return ImpositionSettings(
            paper=str(self.paper_combo.currentData()),
            orientation="portrait" if self.portrait_radio.isChecked() else "landscape",
            layout=self.current_layout(),
            margin_mm=float(self.margin_spin.value()),
            gap_mm=float(self.gap_spin.value()),
            draw_separator=self.separator_check.isChecked(),
        )

    def schedule_preview(self) -> None:
        self.preview_timer.start()

    def update_preview(self) -> None:
        if self.preview_doc is not None:
            self.preview_doc.close()
            self.preview_doc = None

        if not self.invoices:
            self.current_sheet = 0
            self.preview_label.set_source_pixmap(None)
            self.page_label.setText("0/0")
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.update_summary()
            return

        try:
            self.preview_doc = build_imposed_document(self.invoices, self.current_settings())
        except Exception as exc:
            self.preview_label.set_source_pixmap(None)
            self.page_label.setText("0/0")
            self.summary_line.setText(f"预览失败: {exc}")
            self.update_action_states()
            return

        self.current_sheet = min(self.current_sheet, max(self.preview_doc.page_count - 1, 0))
        self.render_current_sheet()
        self.update_summary()

    def render_current_sheet(self) -> None:
        if self.preview_doc is None or self.preview_doc.page_count == 0:
            self.preview_label.set_source_pixmap(None)
            self.page_label.setText("0/0")
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return

        self.current_sheet = max(0, min(self.current_sheet, self.preview_doc.page_count - 1))
        scale = 6.0 if self.fit_preview else max(3.0, min(self.zoom_percent / 100 * 3.2, 6.0))
        pixmap = render_pdf_page(self.preview_doc[self.current_sheet], scale)
        self.preview_label.set_fit_to_window(self.fit_preview)
        if hasattr(self, "preview_scroll"):
            self.preview_scroll.setWidgetResizable(False)
            self.preview_label.set_viewport_size(self.preview_scroll.viewport().size())
        self.preview_label.set_source_pixmap(pixmap)
        if not self.fit_preview:
            self.preview_label.set_zoom_percent(self.zoom_percent)
        else:
            self.zoom_label.setText("宽度")
            if hasattr(self, "fit_button"):
                self.fit_button.setChecked(True)
        self.page_label.setText(f"{self.current_sheet + 1}/{self.preview_doc.page_count}")
        self.prev_button.setEnabled(self.current_sheet > 0)
        self.next_button.setEnabled(self.current_sheet < self.preview_doc.page_count - 1)

    def update_summary(self) -> None:
        invoice_count = len(self.invoices)
        source_pages = 0
        for path in self.invoices:
            try:
                source_pages += self.get_page_count(path)
            except Exception:
                pass
        output_pages = self.preview_doc.page_count if self.preview_doc is not None else 0
        self.summary_line.setText(f"共 {invoice_count} 张票据 / {source_pages} 页原件，输出 {output_pages} 页")
        self.update_action_states()

    def update_action_states(self) -> None:
        has_invoices = bool(self.invoices)
        selected_row = self.invoice_list.currentRow() if hasattr(self, "invoice_list") else -1
        has_selection = 0 <= selected_row < len(self.invoices)
        output_text = self.output_edit.text().strip() if hasattr(self, "output_edit") else ""
        output_path = Path(output_text).expanduser() if output_text else None
        output_exists = output_path.is_file() if output_path is not None else False

        for button_name in ["remove_button", "open_selected_button"]:
            if hasattr(self, button_name):
                getattr(self, button_name).setEnabled(has_selection)
        if hasattr(self, "up_button"):
            self.up_button.setEnabled(has_selection and selected_row > 0)
        if hasattr(self, "down_button"):
            self.down_button.setEnabled(has_selection and selected_row < len(self.invoices) - 1)
        if hasattr(self, "sort_button"):
            self.sort_button.setEnabled(len(self.invoices) > 1)
        if hasattr(self, "clear_button"):
            self.clear_button.setEnabled(has_invoices)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(has_invoices)
        if hasattr(self, "print_button"):
            self.print_button.setEnabled(has_invoices)
        if hasattr(self, "open_output_pdf_button"):
            self.open_output_pdf_button.setEnabled(output_exists)
        if hasattr(self, "open_output_button"):
            self.open_output_button.setEnabled(bool(output_text))
        if hasattr(self, "open_output_dir_button"):
            self.open_output_dir_button.setEnabled(bool(output_text))

    def change_sheet(self, direction: int) -> None:
        if self.preview_doc is None:
            return
        self.current_sheet += direction
        self.render_current_sheet()

    def change_zoom(self, delta: int) -> None:
        self.fit_preview = False
        if hasattr(self, "fit_button"):
            self.fit_button.setChecked(False)
        if hasattr(self, "preview_scroll"):
            self.preview_scroll.setWidgetResizable(False)
        self.zoom_percent = max(50, min(220, self.zoom_percent + delta))
        self.zoom_label.setText(f"{self.zoom_percent}%")
        self.preview_label.set_zoom_percent(self.zoom_percent)
        self.render_current_sheet()

    def fit_preview_to_window(self) -> None:
        self.fit_preview = True
        if hasattr(self, "fit_button"):
            self.fit_button.setChecked(True)
        if hasattr(self, "preview_scroll"):
            self.preview_scroll.setWidgetResizable(False)
            self.preview_label.set_viewport_size(self.preview_scroll.viewport().size())
        self.zoom_label.setText("宽度")
        self.preview_label.set_fit_to_window(True)
        self.render_current_sheet()

    def choose_output_path(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出 PDF",
            self.output_edit.text(),
            "PDF 文件 (*.pdf)",
        )
        if filename:
            path = Path(filename)
            if path.suffix.casefold() != ".pdf":
                path = path.with_suffix(".pdf")
            self.output_edit.setText(str(path))
            self.statusBar().showMessage(f"输出位置：{path}", 2500)

    def export_pdf(self) -> None:
        if not self.ensure_has_invoices():
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "缺少输出位置", "请选择输出 PDF 文件。")
            return
        output = Path(output_text).expanduser()
        if output.suffix.casefold() != ".pdf":
            output = output.with_suffix(".pdf")
            self.output_edit.setText(str(output))
        try:
            self.statusBar().showMessage("正在生成 PDF...")
            pages = save_imposed_pdf(self.invoices, output, self.current_settings())
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", str(exc))
            self.statusBar().showMessage("生成失败", 3000)
            return
        self.update_action_states()
        self.statusBar().showMessage(f"已生成 {pages} 页 PDF：{output.name}", 5000)
        QMessageBox.information(self, "生成完成", f"已生成 {pages} 页 PDF:\n{output}")

    def print_pdf(self) -> None:
        if not self.ensure_has_invoices():
            return

        temp_path = Path(tempfile.gettempdir()) / "invoice_pdf_printer_output.pdf"
        try:
            self.statusBar().showMessage("正在准备打印文件...")
            save_imposed_pdf(self.invoices, temp_path, self.current_settings())
            self.send_pdf_to_printer(temp_path)
            self.statusBar().showMessage("打印任务已提交或已由系统处理", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "打印失败", str(exc))
            self.statusBar().showMessage("打印失败", 3000)

    def send_pdf_to_printer(self, pdf_path: Path) -> None:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(APP_TITLE)
        printer.setCopyCount(self.copy_spin.value())
        printer.setResolution(200)

        printer_name = str(self.printer_combo.currentData() or "")
        if printer_name:
            printer.setPrinterName(printer_name)

        paper_id = QPageSize.PageSizeId.A4
        if self.paper_combo.currentData() == "letter":
            paper_id = QPageSize.PageSizeId.Letter
        printer.setPageSize(QPageSize(paper_id))
        orientation = (
            QPageLayout.Orientation.Portrait
            if self.portrait_radio.isChecked()
            else QPageLayout.Orientation.Landscape
        )
        printer.setPageOrientation(orientation)

        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("打印发票")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        doc = fitz.open(pdf_path)
        painter = QPainter()
        try:
            if not painter.begin(printer):
                raise PdfImpositionError("无法启动打印任务")
            for page_index in range(doc.page_count):
                if page_index > 0 and not printer.newPage():
                    raise PdfImpositionError("无法创建打印页")
                page = doc[page_index]
                scale = printer.resolution() / 72
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = qimage_from_pixmap(pix)
                page_rect = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
                target = fit_rect(image.width(), image.height(), page_rect)
                painter.drawImage(target, image)
        finally:
            if painter.isActive():
                painter.end()
            doc.close()

    def open_output_dir(self) -> None:
        path = Path(self.output_edit.text()).expanduser()
        directory = path.parent if path.suffix else path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def open_output_file(self) -> None:
        path = Path(self.output_edit.text()).expanduser()
        if not path.is_file():
            QMessageBox.information(self, "文件不存在", "请先生成输出 PDF。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def ensure_has_invoices(self) -> bool:
        if self.invoices:
            return True
        QMessageBox.warning(self, "没有发票", "请先添加 PDF 发票。")
        return False


def main() -> int:
    app = QApplication([])
    app.setApplicationName(APP_TITLE)
    window = InvoicePrinterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
