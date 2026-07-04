# Web Toolkit

`WebToolkit` is an internal focused web access implementation used by
`CoreToolkit`. It no longer ships a public builtin registry manifest.

It exposes `web_fetch` without bringing along workspace mutation, shell, LSP,
or interaction tools. Execution delegates to the existing `CoreToolkit` web
implementation so fetch behavior and history compaction stay compatible.
