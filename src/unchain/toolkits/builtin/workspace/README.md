# Workspace Toolkit

`WorkspaceToolkit` is a legacy wrapper for older workspace-oriented consumers.
It no longer ships a public builtin registry manifest. New coding agents should
use `CoreToolkit` as the default bundled surface.

It keeps canonical coding tools such as `read`, `write`, `edit`, `glob`,
`grep`, `shell`, and `lsp`, while keeping stable compatibility names such as
`read_file`, `write_file`, `delete_file`, `move_file`, `terminal_exec`,
`pin_file_context`, and `unpin_file_context`.
