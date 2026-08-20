# DocBook build for the nrlsmf User's Guide (doc/nrlsmf.xml)
#
# Outputs:
#   nrlsmf.html  - single-file HTML (DocBook XSL)
#   nrlsmf.pdf   - PDF via dblatex when available
#   site/        - GitHub Pages bundle (index.html, nrlsmf.pdf, resources/)
#
# Typical packages (Debian/Ubuntu):
#   sudo apt install xsltproc docbook-xml docbook-xsl dblatex
#
# Typical packages (Fedora/RHEL):
#   sudo dnf install libxslt docbook-style-xsl docbook-dtds dblatex

SOURCE   = nrlsmf.xml
HTML_OUT = nrlsmf.html
PDF_OUT  = nrlsmf.pdf
SITE_DIR = site

XSLTPROC ?= xsltproc
DBLATEX  ?= dblatex

# DocBook XSL stylesheet locations vary by distribution.
DOCBOOK_XSL_HTML ?= $(firstword $(wildcard \
	/usr/share/xml/docbook/stylesheet/docbook-xsl-nons/html/docbook.xsl \
	/usr/share/sgml/docbook/xsl-stylesheets/nons/html/docbook.xsl \
	/usr/share/xml/docbook/stylesheet/nwalsh/html/docbook.xsl \
	/usr/local/share/docbook-xsl/html/docbook.xsl \
	/opt/local/share/xsl/docbook-xsl/html/docbook.xsl))

.PHONY: all html pdf pages clean check-tools

all: html pdf

html: $(HTML_OUT)

pdf: $(PDF_OUT)

pages: html pdf
	rm -rf $(SITE_DIR)
	mkdir -p $(SITE_DIR)/resources
	cp $(HTML_OUT) $(SITE_DIR)/index.html
	cp $(PDF_OUT) $(SITE_DIR)/$(PDF_OUT)
	cp -r resources/. $(SITE_DIR)/resources/

$(HTML_OUT): $(SOURCE)
	@test -n "$(DOCBOOK_XSL_HTML)" || ( \
		echo "error: DocBook HTML stylesheet not found." >&2; \
		echo "Install docbook-xsl (or set DOCBOOK_XSL_HTML=...)" >&2; \
		exit 1)
	@test -x "$$(command -v $(XSLTPROC) 2>/dev/null)" || ( \
		echo "error: $(XSLTPROC) not found (install xsltproc / libxslt)" >&2; \
		exit 1)
	$(XSLTPROC) --output $@ "$(DOCBOOK_XSL_HTML)" $<

$(PDF_OUT): $(SOURCE)
	@test -x "$$(command -v $(DBLATEX) 2>/dev/null)" || ( \
		echo "error: $(DBLATEX) not found (install dblatex for PDF output)" >&2; \
		exit 1)
	$(DBLATEX) -P table.in.float=0 -o $@ $<

clean:
	rm -rf $(SITE_DIR)
	rm -f $(HTML_OUT) $(PDF_OUT) *.aux *.log *.out *.toc *.idx *.ilg *.ind

check-tools:
	@echo "XSLTPROC          = $(XSLTPROC)"
	@echo "DBLATEX           = $(DBLATEX)"
	@echo "DOCBOOK_XSL_HTML  = $(DOCBOOK_XSL_HTML)"
	@command -v $(XSLTPROC) >/dev/null && echo "xsltproc: ok" || echo "xsltproc: missing"
	@command -v $(DBLATEX) >/dev/null && echo "dblatex: ok" || echo "dblatex: missing"
