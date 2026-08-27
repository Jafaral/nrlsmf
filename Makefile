default: elastic

elastic:
	make -C makefiles -f Makefile.linux elastic
	cp -u makefiles/nrlsmf .

docs:
	$(MAKE) -C doc all

docs-html:
	$(MAKE) -C doc html

docs-pdf:
	$(MAKE) -C doc pdf

docs-site:
	$(MAKE) -C doc pages

install: elastic
	# Unlink first so a running nrlsmf does not cause ETXTBSY ("Text file busy").
	sudo rm -f /usr/bin/nrlsmf
	sudo cp -f ./nrlsmf /usr/bin/nrlsmf

clean:
	make -C makefiles -f Makefile.linux clean
	rm -f nrlsmf

clean-docs:
	$(MAKE) -C doc clean

.PHONY: docs docs-html docs-pdf docs-site clean-docs