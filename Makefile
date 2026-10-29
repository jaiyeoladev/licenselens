.PHONY: install run scan check test lint docker

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

run:
	python -m licenselens serve --reload

scan:
	python -m licenselens scan

check:
	python -m licenselens check

test:
	pytest -q

lint:
	ruff check .

docker:
	docker compose up --build
