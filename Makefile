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

install: elastic
	sudo cp -u ./nrlsmf /usr/bin/nrlsmf

clean:
	make -C makefiles -f Makefile.linux clean
	rm -f nrlsmf

clean-docs:
	$(MAKE) -C doc clean