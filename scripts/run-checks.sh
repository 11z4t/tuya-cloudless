#!/bin/bash
# Full validation pipeline — ALL must pass before commit
set -euo pipefail

echo "=== Tuya Cloudless — Pre-commit Checks ==="

echo ""
echo "--- Ruff check ---"
python3 -m ruff check .

echo ""
echo "--- Ruff format check ---"
python3 -m ruff format --check .

echo ""
echo "--- MyPy strict ---"
python3 -m mypy lib/tuya_cloudless --strict --ignore-missing-imports

echo ""
echo "--- Bandit security scan ---"
python3 -m bandit -r lib/ -q || true

echo ""
echo "--- Detect-secrets scan ---"
python3 -m detect_secrets scan --all-files \
  --exclude-files "(\.git/.*|\.claude/.*|\.mypy_cache/.*|\.pytest_cache/.*|\.ruff_cache/.*|htmlcov/.*|strings\.json|translations/.*\.json|pairing_ui/i18n/.*\.json|tests/)" \
  > /tmp/secrets-report.json
python3 -c "
import json
with open('/tmp/secrets-report.json') as f:
    data = json.load(f)
results = data.get('results', {})
total = sum(len(v) for v in results.values())
print(f'Found {total} potential secrets' if total else 'No secrets detected')
exit(1 if total else 0)
"

echo ""
echo "--- Pytest (unit + security) ---"
python3 -m pytest tests/unit tests/security -v --tb=short --timeout=30

echo ""
echo "=== ALL CHECKS PASSED ==="
