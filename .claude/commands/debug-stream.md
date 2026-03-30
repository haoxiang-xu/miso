# Debug Streaming Issue

Diagnose why a chat stream is stuck, erroring, or not completing in the PuPu + miso runtime stack.

## Arguments
- $ARGUMENTS: Description of the issue (e.g. "stream stuck after request_messages frame with anthropic provider")

## Steps

1. Identify which layer the issue is in by reading the symptom:
   - **No stream starts**: Check `PuPu/electron/main/services/miso/service.js` (startMisoStream)
   - **Stream starts but no tokens**: Check provider fetch in `src/unchain/providers/model_io.py`
   - **Stuck after tool_call**: Check `PuPu/miso_runtime/server/miso_adapter.py` (_make_tool_confirm_callback)
   - **No continuation prompt**: Check max_iterations and _make_continuation_callback
   - **Error not surfacing**: Check SSE parsing in `PuPu/electron/preload/stream/miso_stream_client.js`

2. Trace the full request path:
   ```
   PuPu frontend (use_chat_stream.js)
     → Electron IPC (miso_bridge.js → register_handlers.js)
       → miso_runtime Flask server (routes.py → chat_stream_v2)
         → miso_adapter.py (stream_chat_events → agent.run)
           → unchain kernel (loop.py → model_io.py)
             → Provider SDK (OpenAI/Anthropic/Gemini/Ollama)
   ```

3. Check for common issues:
   - Context window overflow (count messages × tools vs max_context_window_tokens)
   - Missing API key
   - Tool confirmation deadlock (threading.Event.wait() with no timeout)
   - Provider SDK timeout/retry behavior
   - SSE parsing edge cases (missing \n\n delimiter)

4. Read the relevant source files and identify the root cause
5. Suggest a fix with specific file and line references
