.PHONY: build clean

build: .venv

.venv:
	python3 -m venv $(CURDIR)/.venv
	$(CURDIR)/.venv/bin/pip install --upgrade pip poetry
	$(CURDIR)/.venv/bin/poetry install

clean:
	rm -rf $(CURDIR)/.venv