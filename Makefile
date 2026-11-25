PYTHON ?= python3
CHECK   := $(PYTHON) scripts/check_readme.py

.PHONY: help test test-online test-all

help:
	@echo "make test        # offline README checks (structure, TOC, tables, links, assets)"
	@echo "make test-online # + GitHub API: repos exist, badges match, star ordering"
	@echo "make test-all    # + SaaS site reachability (slow, needs open network)"

test:
	$(CHECK)

test-online:
	$(CHECK) --online

test-all:
	$(CHECK) --online --check-external
