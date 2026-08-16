PYTHON ?= python3

.PHONY: check go-check vision-check docs-check safety-check hero-check

check:
	bash scripts/check.sh

go-check:
	bash scripts/validate-go.sh

vision-check:
	$(PYTHON) scripts/validate_vision.py

docs-check:
	$(PYTHON) scripts/check_docs.py

safety-check:
	$(PYTHON) scripts/check_public_safety.py

hero-check:
	$(PYTHON) scripts/check_hero.py
