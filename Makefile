APP_NAME := InvoicePress
ENTRYPOINT := invoice_pdf_printer/__main__.py
ASSET_SRC := invoice_pdf_printer/assets
ASSET_DST := invoice_pdf_printer/assets
VERSION ?= v0.1.0

POETRY := poetry
PYINSTALLER := $(POETRY) run pyinstaller

UNAME_S := $(shell uname -s 2>/dev/null)

ifeq ($(OS),Windows_NT)
	ADD_DATA := --add-data "$(ASSET_SRC);$(ASSET_DST)"
	CLEAN_CMD := powershell -NoProfile -Command "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build,dist"
	ZIP_WINDOWS_CMD := powershell -NoProfile -Command "Compress-Archive -Path 'dist/$(APP_NAME)/*' -DestinationPath 'dist/$(APP_NAME)-windows.zip' -Force"
else
	ADD_DATA := --add-data "$(ASSET_SRC):$(ASSET_DST)"
	CLEAN_CMD := rm -rf build dist
	ZIP_WINDOWS_CMD := @echo "Windows package must be created on Windows."
endif

PYINSTALLER_FLAGS := --name $(APP_NAME) --noconfirm --clean --windowed $(ADD_DATA) --hidden-import=fitz

.PHONY: help install run cli clean build build-mac build-windows package-mac package-windows verify release-tag

help:
	@echo "InvoicePress build targets:"
	@echo "  make install          Install runtime and dev dependencies with Poetry"
	@echo "  make run              Start the desktop client"
	@echo "  make cli              Generate the default imposed PDF"
	@echo "  make build-mac        Build macOS .app and zip bundle on macOS"
	@echo "  make build-windows    Build Windows app folder and zip bundle on Windows"
	@echo "  make build            Dispatch to build-mac on macOS or build-windows on Windows"
	@echo "  make clean            Remove build artifacts"
	@echo "  make verify           Run lightweight project checks"
	@echo "  make release-tag VERSION=v0.1.0"
	@echo "                          Create and push a Git tag that triggers GitHub Release packaging"

install:
	$(POETRY) install --with dev

run:
	$(POETRY) run invoicepress

cli:
	$(POETRY) run pdf-impose

clean:
	$(CLEAN_CMD)

verify:
	$(POETRY) check
	$(POETRY) run python -m py_compile invoice_pdf_printer/app.py invoice_pdf_printer/cli.py invoice_pdf_printer/imposition.py
	$(POETRY) run pdf-impose --layout four-grid -o /tmp/invoicepress-four-grid-check.pdf

build:
ifeq ($(OS),Windows_NT)
	$(MAKE) build-windows
else ifeq ($(UNAME_S),Darwin)
	$(MAKE) build-mac
else
	@echo "Unsupported build host: $(UNAME_S). Use build-mac on macOS or build-windows on Windows."
	@exit 1
endif

build-mac: install clean
	@if [ "$(UNAME_S)" != "Darwin" ]; then echo "build-mac must run on macOS."; exit 1; fi
	$(PYINSTALLER) $(PYINSTALLER_FLAGS) $(ENTRYPOINT)
	ditto -c -k --keepParent "dist/$(APP_NAME).app" "dist/$(APP_NAME)-macos.zip"
	@echo "macOS app: dist/$(APP_NAME).app"
	@echo "macOS zip: dist/$(APP_NAME)-macos.zip"

build-windows:
ifeq ($(OS),Windows_NT)
	$(MAKE) install
	$(MAKE) clean
	$(PYINSTALLER) $(PYINSTALLER_FLAGS) $(ENTRYPOINT)
	$(ZIP_WINDOWS_CMD)
	@echo "Windows app folder: dist/$(APP_NAME)"
	@echo "Windows zip: dist/$(APP_NAME)-windows.zip"
else
	@echo "build-windows must run on Windows."
	@exit 1
endif

package-mac: build-mac

package-windows: build-windows

release-tag:
	git tag -a $(VERSION) -m "Release $(VERSION)"
	git push origin $(VERSION)

debug:
	$(POETRY) run invoicepress
.PHONY: debug
