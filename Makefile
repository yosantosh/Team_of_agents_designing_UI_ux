.PHONY: install demo run serve test lint clean scan-target-ui

install:
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -e ".[dev]"

demo:
	.venv/bin/uiux demo

run:
	.venv/bin/uiux run "Redesign the profile screen for a premium, minimal social app" --mode design --target-ui

serve:
	.venv/bin/uiux serve --host 127.0.0.1 --port 8765

test:
	.venv/bin/pytest -q

scan-target-ui:
	.venv/bin/uiux target-ui scan ./target_ui_example --analyze

clean:
	rm -rf .runtime artifacts/runs .pytest_cache
