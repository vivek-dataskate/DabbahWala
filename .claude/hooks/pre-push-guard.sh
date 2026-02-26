#!/bin/bash
# Pre-tool use hook: block direct git push to main

input=$(cat)
tool=$(echo "$input" | jq -r '.tool_name // ""')
command=$(echo "$input" | jq -r '.tool_input.command // ""')

# Only inspect Bash tool calls
if [[ "$tool" != "Bash" ]]; then
  exit 0
fi

# Block: git push ... main (direct push to main)
if echo "$command" | grep -qE 'git push[^|]*\bmain\b'; then
  echo "BLOCKED: Direct push to main is not allowed. Use a feature branch and create a PR instead." >&2
  exit 2
fi

# Block: git push --force / -f (but allow --force-with-lease for rebased feature branches)
if echo "$command" | grep -qE 'git push[^|]*(--force[^-]|--force$|-f\b)'; then
  echo "BLOCKED: Force push is not allowed. Use --force-with-lease only when rebasing your own feature branch." >&2
  exit 2
fi

exit 0
