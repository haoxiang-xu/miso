# Workspace Toolkit

Structured file and directory access for coding agents.

## Included tools

- `read_files`
- `list_directories`
- `search_text`
- `write_file`
- `read_lines`
- `insert_lines`
- `replace_lines`
- `delete_lines`
- `pin_file_context`
- `unpin_file_context`

## Design

- Reads return JSON-shaped results instead of shell text blobs.
- File mutation is limited to whole-file writes and line-range edits.
- Session pinning integrates with the workspace pin runtime rather than relying on ad hoc prompts.
- Large source files can auto-upgrade to AST output through `read_files`.
