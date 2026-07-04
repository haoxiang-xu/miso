# Interaction Toolkit

`InteractionToolkit` is an internal focused human interaction implementation
used by `CoreToolkit`. It no longer ships a public builtin registry manifest.

It currently exposes the reserved `ask_user_question` runtime tool, which lets
an agent request structured user input and suspend the run until a response is
available.
