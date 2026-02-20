PYTHON ?= venv/bin/python
RUFF_TARGETS = controller/config.py controller/delta_engine.py controller/interface.py controller/models.py tests
MYPY_TARGETS = controller/config.py controller/delta_engine.py controller/interface.py controller/models.py tests

.PHONY: run test lint format typecheck check

run:
	$(PYTHON) -m controller --config config.yaml

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

lint:
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then \
		$(PYTHON) -m ruff check $(RUFF_TARGETS); \
	else \
		echo "ruff not installed; running syntax check with compileall"; \
		$(PYTHON) -m compileall -q controller tests; \
	fi

format:
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then \
		$(PYTHON) -m ruff format $(RUFF_TARGETS); \
	else \
		echo "ruff not installed; format skipped"; \
	fi

typecheck:
	@if $(PYTHON) -m mypy --version >/dev/null 2>&1; then \
		$(PYTHON) -m mypy $(MYPY_TARGETS); \
	else \
		echo "mypy not installed; typecheck skipped"; \
	fi

check: lint test
