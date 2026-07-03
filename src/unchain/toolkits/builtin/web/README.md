# Web Toolkit

`WebToolkit` is the focused public web access surface.

It exposes `web_fetch` without bringing along workspace mutation, shell, LSP,
or interaction tools. Execution delegates to the existing `CoreToolkit` web
implementation so fetch behavior and history compaction stay compatible.
