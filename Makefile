.PHONY: test test-report demo live-demo

test:
	python3 -m pytest -q

test-report:
	{ echo "# Test results"; echo "# Command: python3 -m pytest -v -o addopts=''"; echo; python3 -m pytest -v -o addopts=''; } > examples/test-results.txt 2>&1

demo:
	python3 scripts/build_examples.py

live-demo:
	@echo "See README.md for the live-opencode-local commands."
