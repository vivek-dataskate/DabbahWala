---
description: Commit and push all current changes to the active feature branch. Use when the user says "commit", "commit my changes", "save my work", or similar.
---

# DabbahWala Commit & Push

Commit all staged and unstaged changes to the current branch and push to origin.

## Workflow

1. **Verify branch** — confirm current branch starts with `claude/`. If on `main`, stop and tell the user to switch to a feature branch.

2. **Sync with main** — run `git fetch origin main && git rebase origin/main`. If conflicts arise, resolve them before proceeding.

3. **Stage changes** — `git add` only relevant files (avoid accidentally staging `.env`, secrets, or unrelated files). Prefer explicit file names over `git add -A`.

4. **Write commit message** — summarise *why* the change was made (not just what). One line title, optional body. End every commit message with:
   ```
   https://claude.ai/code/session_01KFDkgbfakqfRJD9TCJ6oaE
   ```

5. **Commit** — use a HEREDOC to preserve formatting:
   ```bash
   git commit -m "$(cat <<'EOF'
   Your message here

   https://claude.ai/code/session_01KFDkgbfakqfRJD9TCJ6oaE
   EOF
   )"
   ```

6. **Push** — `git push -u origin <branch-name>`. On failure, retry up to 4 times with exponential backoff (2s, 4s, 8s, 16s). Never force-push unless the user explicitly asks.

7. **Report** — show the commit hash and confirm the push succeeded.

## After committing

Ask the user: "Would you like me to create a PR, merge to main, and delete the branch?"

Do NOT create the PR automatically — always wait for explicit permission.
