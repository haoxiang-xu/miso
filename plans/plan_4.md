# Add Control Mouse Toolkit for Mouse Control, Screenshot, and Computer Operation

## Summary
This plan aims to add a new builtin toolkit named ControlMouseToolkit to the unchain project. The toolkit will enable controlling the mouse (move, click, drag, scroll), keyboard input, screen information retrieval, and screenshot capability with saving files safely under the workspace. The toolkit will be cross-platform and use third-party libraries such as pyautogui and Pillow. Control operations will require user confirmation for safety. The implementation will follow existing builtin toolkit conventions including toolkit.toml and runtime registration.

## Goal
Implement a new builtin toolkit named ControlMouseToolkit to enable mouse control (move, click, drag, scroll), keyboard input, screen information retrieval, and screenshot functionality with workspace-safe file saving. The toolkit should require confirmation for control operations and be cross-platform. It should use third-party dependencies such as pyautogui and Pillow for implementation.

## Constraints
- Cross-platform support, workspace-safe file operations, user confirmation for control actions.

## Steps
- [pending] Create a new directory src/unchain/toolkits/builtin/control_mouse/ and add toolkit.toml manifest.
- [pending] Implement ControlMouseToolkit class with mouse control methods: move, click, drag, scroll.
- [pending] Implement keyboard input and shortcut methods.
- [pending] Implement screen information retrieval methods (screen resolution, current mouse position).
- [pending] Implement screenshot methods supporting full screen and region capture, saving output files under workspace path using _resolve_workspace_path().
- [pending] Ensure all control operations require user confirmation to execute.
- [pending] Register the toolkit in the runtime and ensure toolkit.toml listing consistency.
- [pending] Add minimal smoke tests for the new toolkit.
- [pending] Update project documentation to describe the new ControlMouseToolkit and its usage.
- [pending] Obtain user approval for adding third-party dependencies: pyautogui, Pillow, and optionally pynput and mss for performance and stability improvements.

## Key Changes
- Add a new builtin toolkit directory with implementation and manifest files. Modify runtime registration to include the new toolkit. Add third-party dependencies to the project requirements or installation scripts.

## Public Interfaces
- ControlMouseToolkit with methods for mouse control, keyboard input, screen info, and screenshot. Toolkit manifest and registration updates.

## Test Cases
- Smoke tests verifying mouse move, click, screenshot creation, and file saving in workspace.

## Assumptions
- User agrees to add third-party dependencies needed for implementation. Basic window/app control can be limited initially. User confirms control operations require explicit confirmation.

## References
- unchain builtin toolkit development guidelines, pyautogui and Pillow official documentation.

## Open Questions
- Does the user approve the addition of third-party dependencies? Should basic app/window control be included in the initial version or deferred?
