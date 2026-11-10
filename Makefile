.PHONY: test demo live-demo

test:
	python3 -m pytest -q

demo:
	python3 scripts/build_examples.py

live-demo:
	@echo "See README.md for the live-opencode-local commands."
