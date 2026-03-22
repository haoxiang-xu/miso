# Notebook Tests

Copy [single_test_template.ipynb](/Users/red/Desktop/GITRepo/miso/tests/evals/templates/single_test_template.ipynb)
into this folder when you want to create a new benchmark notebook.

Recommended pattern:

- one notebook = one test
- one `MODEL_SPEC` per run by default
- judge model defaults centrally to `claude-opus-4-6`
- define `EVAL_RULES`, `TASK_PROMPT`, `WORKSPACE_CONFIG`, and `TOOLKIT_CONFIG` inside the notebook cells

Artifacts are written under `tests/evals/artifacts/<test_id>/<timestamp>/` unless you override `ARTIFACTS_ROOT`.
