#!/bin/bash
# Post-tool use hook: run flake8 on Python files after Write/Edit

input=$(cat)
tool=$(echo "$input" | jq -r '.tool_name // ""')

# Only run after file edits
if [[ "$tool" != "Write" && "$tool" != "Edit" ]]; then
  exit 0
fi

file_path=$(echo "$input" | jq -r '.tool_input.file_path // ""')

# Only lint Python files
if [[ "$file_path" != *.py ]]; then
  exit 0
fi

# Skip if file doesn't exist
if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# Skip if flake8 not installed
if ! command -v flake8 &>/dev/null; then
  exit 0
fi

# Run flake8 — ignore line length (E501) and allow long imports (W503)
result=$(flake8 --max-line-length=120 --ignore=W503,E501 "$file_path" 2>&1)
exit_code=$?

if [[ $exit_code -ne 0 ]]; then
  echo "flake8 warnings in $file_path:" >&2
  echo "$result" >&2
  # Exit 1 = show warning but don't block (exit 2 would block the action)
  exit 1
fi

exit 0
