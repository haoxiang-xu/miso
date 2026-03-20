# Tetris Beginner Game Test

This folder is a persistent notebook-driven benchmark for asking a coding model to build
a Tetris game for a beginner user.

Rules for this test:

- the workspace root is this folder
- the candidate model may use:
  - `request_user_input`
  - workspace tools
  - terminal tools
- if the model asks a question, the notebook saves a suspended session state
- you answer in the notebook, then resume the same session

Expected app target:

- build the game under `app/`
- avoid editing `artifacts/`
- avoid editing the notebook unless explicitly intended
