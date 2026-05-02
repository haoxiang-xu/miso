# Git Toolkit

Local Git workflow tools for status inspection, diff viewing, staging, unstaging, and committing — all workspace-scoped with fixed argument templates.

## Tools

| Tool | Confirmation | Description |
|------|-------------|-------------|
| `git_status` | No | Show branch, staged, unstaged, and untracked file status |
| `git_diff` | No | Unified diff for worktree or staged changes, with optional path filter |
| `git_stage` | Yes | Stage specific files for the next commit |
| `git_unstage` | Yes | Remove specific files from the staging area |
| `git_commit` | Yes (code diff preview) | Commit staged changes with a message |

## Security model

- No arbitrary `args` accepted — each tool uses a fixed argv template
- All paths validated against workspace roots; repo root must also be within workspace roots
- `paths` parameters reject flag injection (`-flag`), pathspec magic (`:path`), and directory traversal
- `git_commit` only commits already-staged content and does not skip hooks

## Usage note

Do not load `GitToolkit` and `ExternalAPIToolkit` simultaneously — both register `git_status`, `git_diff`, and `git_commit`, causing tool name conflicts.
