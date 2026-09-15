# InvoicePress

一个用于**整理、拼版和打印发票 PDF** 的桌面工具，同时提供可自动化的命令行接口。

InvoicePress 面向需要批量打印电子发票、收据或其他 PDF 凭证的场景：导入多个 PDF 后，可以按文件名自然排序或手动调整顺序，在界面中预览打印效果，再导出拼版 PDF 或调用系统打印对话框完成打印。

> 当前版本：`1.0.0` · 许可证：[MIT](LICENSE)

## 它能做什么

- 提供 macOS 与 Windows 桌面客户端，支持导入 PDF、预览、导出和系统打印。
- 支持单文件、多个文件或指定目录中的 PDF；目录只扫描第一层，不递归扫描子目录。
- 按自然顺序排列文件名，例如 `invoice-2.pdf` 会排在 `invoice-10.pdf` 前。
- 可以拖拽排序，也可以用上移、下移按钮调整打印顺序。
- 支持 A4 与 Letter 纸张、纵向与横向，以及四种拼版方式。
- 可设置外边距、版位间距、裁剪分隔线和最大输出页数。
- 会拒绝加密 PDF，并避免将生成文件覆盖输入文件。

## 拼版方式

| 参数值 | 每张输出纸 | 适用场景 |
| --- | --- | --- |
| `single` | 1 页 PDF | 原尺寸单页打印或预览 |
| `two-vertical` | 2 页，上下排列 | 默认模式；适合 A4 纵向打印 |
| `two-horizontal` | 2 页，左右排列 | 适合横向纸张 |
| `four-grid` | 4 页，2×2 排列 | 节省纸张的批量打印 |

除单页模式外，默认会绘制浅灰色分隔线，便于裁切；可在桌面端关闭，或在命令行中使用 `--no-separator` 关闭。

## 快速开始

### 运行桌面客户端

**前置要求**：Python `>=3.10,<3.14` 和 [Poetry](https://python-poetry.org/)。

```bash
# 克隆仓库后，在项目根目录执行
poetry install
poetry run invoicepress
```

客户端打开后：

1. 点击“添加 PDF”或“添加目录”；也可以让程序扫描当前目录中的 PDF。
2. 在左侧列表拖拽调整顺序；双击条目可打开原始 PDF。
3. 在右侧选择纸张、方向、布局、边距和分隔线，中央区域会实时预览。
4. 点击“生成 PDF”导出文件，或点击“打印”打开系统打印对话框。

兼容的桌面端入口：

```bash
poetry run invoice-pdf-printer
# 或
python3 -m invoice_pdf_printer
```

### 常用快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+O` | 添加 PDF 文件 |
| `Ctrl+Shift+O` | 添加目录 |
| `Delete` / `Backspace` | 移除选中项 |
| `Ctrl+R` | 重新扫描当前目录 |
| `Ctrl+S` | 导出拼版 PDF |
| `Ctrl+P` | 打印 |
| `+` / `-` | 放大 / 缩小预览 |
| `0` | 预览适合页面 |

## 命令行使用

命令行入口为 `pdf-impose`。未提供输入文件时，它会扫描当前工作目录中的 `*.pdf`，按自然顺序拼版，并默认生成 `print_2up_portrait.pdf`。

```bash
poetry run pdf-impose
```

也可以传入 PDF 文件和目录（目录只读取其中直接包含的 PDF）：

```bash
poetry run pdf-impose invoices/ invoice-001.pdf -o output/print.pdf
```

### 常用示例

```bash
# A4 纵向、上下二合一（默认布局），指定输出文件
poetry run pdf-impose -o result.pdf

# A4 纵向四宫格
poetry run pdf-impose invoices/ --paper a4 --orientation portrait --layout four-grid

# A4 横向、左右二合一，并关闭裁剪分隔线
poetry run pdf-impose invoice-1.pdf invoice-2.pdf \
  --orientation landscape \
  --layout two-horizontal \
  --no-separator

# 设置 8 mm 外边距、3 mm 版位间距，最多生成两张输出纸
poetry run pdf-impose invoices/ --margin-mm 8 --gap-mm 3 --max-sheets 2
```

兼容旧命令：

```bash
poetry run pdf-2up-portrait
# 或
python3 pdf_2up_portrait.py
```

### 参数说明

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `inputs ...` | PDF 文件或目录；省略时扫描当前目录 | 无 |
| `-o`, `--output` | 输出 PDF 路径 | `print_2up_portrait.pdf` |
| `--paper` | 纸张大小：`a4`、`letter` | `a4` |
| `--orientation` | 方向：`portrait`、`landscape` | `portrait` |
| `--layout` | `single`、`two-vertical`、`two-horizontal`、`four-grid` | `two-vertical` |
| `--margin-mm` | 外边距，单位 mm | `5` |
| `--gap-mm` | 版位之间的间距，单位 mm | `2` |
| `--no-separator` | 不绘制裁剪分隔线 | 默认绘制 |
| `--max-sheets` | 限制生成的输出纸张页数 | 不限制 |

## 输入与输出规则

- 一个输入 PDF 中的每一页都会按顺序参与拼版。
- 自动扫描会跳过当前输出文件和常见历史输出名，避免把已生成的打印文件再次作为输入。
- 输入文件为空、PDF 没有页面、文件无法读取、加密文件需要密码，或边距 / 间距挤压到没有可用版位时，程序会显示错误。
- 输出路径不能等于任一输入 PDF，以防覆盖原始发票。
- `--max-sheets` 限制的是**输出 PDF 的页数**，而不是输入 PDF 的页数。

## 开发与验证

开发或打包时安装开发依赖：

```bash
poetry install --with dev
```

可用的 Makefile 命令：

```bash
make help        # 查看全部命令
make run         # 启动桌面客户端
make cli         # 对当前目录中的 PDF 执行默认拼版
make clean       # 删除 build/ 和 dist/
```

基础静态检查：

```bash
poetry check
poetry run python -m py_compile invoice_pdf_printer/app.py invoice_pdf_printer/cli.py invoice_pdf_printer/imposition.py
```

`make verify` 还会执行一次实际拼版，因此请先在项目根目录准备至少一个可读取的 PDF 文件；它会生成 `/tmp/invoicepress-four-grid-check.pdf`。

## 本地打包

PyInstaller 不适合可靠地跨系统构建桌面应用，请在目标操作系统上分别打包。

### macOS

```bash
make build-mac
```

生成：

```text
dist/InvoicePress.app
dist/InvoicePress-macos.zip
```

### Windows

在 Windows 的 PowerShell、Git Bash、MSYS2 或其他可运行 `make` 的环境中执行：

```bash
make build-windows
```

生成：

```text
dist/InvoicePress/
dist/InvoicePress-windows.zip
```

当前项目没有配置代码签名、公证或安装器；macOS / Windows 可能提示来自未知开发者，请按系统提示确认运行。

## 项目结构

```text
invoice_pdf_printer/
  app.py                       # PySide6 桌面客户端
  cli.py                       # 命令行参数与入口
  imposition.py                # PDF 发现、排序、拼版和导出核心逻辑
  assets/                      # 客户端静态资源
Makefile                       # 安装、运行、验证和打包命令
pyproject.toml                 # Poetry 配置与命令行入口
```

## 许可证

InvoicePress 使用 [MIT License](LICENSE) 开源。欢迎提交 Issue 和 Pull Request；提交前请运行基础检查，并在修改用户可见行为时同步更新本 README。
