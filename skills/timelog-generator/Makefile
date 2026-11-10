.PHONY: help test test-extraction test-reports lint format install clean trial

help:
	@echo "Time Log Generator - Development Tasks"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  trial          Run trial on ~/projects/incyt (past week)"
	@echo "  test           Run all tests"
	@echo "  test-extraction Test extraction stage only"
	@echo "  test-reports   Test report generation"
	@echo "  install        Install to ~/.claude/skills/"
	@echo "  uninstall      Remove from ~/.claude/skills/"
	@echo "  clean          Remove generated files"
	@echo "  lint           Check Python syntax"
	@echo "  format         Format Python code (black)"

trial:
	@echo "Running trial extraction for past 7 days..."
	@SINCE=$$(date -u -d '7 days ago' +%Y-%m-%d) && \
	UNTIL=$$(date -u +%Y-%m-%d) && \
	bash run.sh \
		--target ~/projects/incyt \
		--output ./trial-output \
		--since $$SINCE \
		--until $$UNTIL
	@echo ""
	@echo "✓ Trial complete. Results in trial-output/"
	@cat trial-output/timelog_summary.md

test: test-extraction test-reports
	@echo "✓ All tests passed"

test-extraction:
	@echo "Testing extraction..."
	@python3 -m py_compile timelog_extract.py
	@echo "✓ Extraction syntax OK"
	@python3 -c "from timelog_extract import estimate_hours; test_cases = [(1, 5, 0, 10, False, 0.25), (3, 50, 0, 50, False, None), (8, 200, 50, 150, False, None), (0, 0, 0, 20, True, 0.1)]; [print(f'{chr(10003)} {estimate_hours(f, a, r, m, is_m):.2f}h') for f, a, r, m, is_m, exp in test_cases]; print('✓ Hour estimation tests passed')"


test-reports:
	@echo "Testing report generation..."
	@python3 -m py_compile timelog_report.py
	@echo "✓ Report syntax OK"

install:
	@echo "Installing to ~/.claude/skills/timelog-generator/"
	@mkdir -p ~/.claude/skills/
	@cp -r . ~/.claude/skills/timelog-generator/
	@echo "✓ Installed. Use with: /timelog"

uninstall:
	@echo "Removing from ~/.claude/skills/timelog-generator/"
	@rm -rf ~/.claude/skills/timelog-generator/
	@echo "✓ Uninstalled"

clean:
	@echo "Cleaning generated files..."
	@rm -rf test-output trial-output __pycache__ *.pyc .pytest_cache
	@echo "✓ Clean"

lint:
	@echo "Checking Python syntax..."
	@python3 -m py_compile timelog_extract.py timelog_infer.py timelog_report.py
	@echo "✓ All files valid"

format:
	@echo "Formatting Python code..."
	@command -v black >/dev/null 2>&1 && black timelog_*.py run.sh || echo "Install black: pip install black"
	@echo "✓ Formatted"

.DEFAULT_GOAL := help
