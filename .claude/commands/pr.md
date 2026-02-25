---
description: Create a pull request, merge to main, and delete the branch for DabbahWala. Use when the user says "create PR", "open PR", "merge to main", "ship this", or gives explicit permission to merge.
---

# DabbahWala Pull Request Workflow

Create a PR, merge it to main, and clean up the branch — all via the GitHub API.

## Prerequisites

- GitHub token: read from `GITHUB_TOKEN` env var, or from `~/.claude/CLAUDE.md` if empty
- Repo: `vivek-dataskate/DabbahWala`
- Base branch: always `main`

## Workflow

### 0. Update docs first

Run `/update-docs` — review the branch diff and update SYSTEM.md, FEATURES.md, and CLAUDE.md as needed.
Commit any doc changes before proceeding.

### 1. Sync & verify

```bash
git fetch origin main
git rebase origin/main
```

If rebase conflicts exist, resolve them first. Never proceed with a dirty tree.

### 2. Push branch

```bash
git push -u origin <current-branch>
```

Retry up to 4× with exponential backoff (2s, 4s, 8s, 16s) on network failures.

### 3. Create PR via GitHub API

```bash
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/vivek-dataskate/DabbahWala/pulls \
  -d '{
    "title": "<concise title under 70 chars>",
    "body": "<summary>",
    "head": "<branch-name>",
    "base": "main"
  }'
```

PR body format:
```
## Summary
- <bullet 1>
- <bullet 2>

## Test plan
- [ ] <test item>

https://claude.ai/code/session_01KFDkgbfakqfRJD9TCJ6oaE
```

Extract the PR number from the response.

### 4. Merge immediately

```bash
curl -s -X PUT \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/vivek-dataskate/DabbahWala/pulls/<PR_NUMBER>/merge \
  -d '{"merge_method": "squash", "commit_title": "<same as PR title>"}'
```

### 6. Delete branch

```bash
curl -s -X DELETE \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/vivek-dataskate/DabbahWala/git/refs/heads/<branch-name>
```

### 7. Sync local main

```bash
git checkout main && git pull origin main
```

## Notes

- Never push to `main` directly — always use a PR
- Branch names must start with `claude/` and end with the session ID suffix
- Render auto-deploys on merge to `main` (build + migrations run automatically)
- Multiple sessions may be active — always rebase before pushing to avoid conflicts
