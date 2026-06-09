# Agent Reach Toolkit

`AgentReachToolkit` exposes low-risk read-only helpers around
[Agent-Reach](https://github.com/Panniantong/Agent-Reach).

It does not install Agent-Reach, import cookies, configure credentials, write
social posts, or modify the local system. Install Agent-Reach separately when
you want the status and web reader helpers to use the upstream package:

```bash
pip install agent-reach
```

For the YouTube metadata helper, ensure `yt-dlp` is available on `PATH`. It is
included by Agent-Reach, but this toolkit only checks for the executable and
never installs it automatically.

## Tools

- `agent_reach_status` checks Agent-Reach channel availability.
- `agent_reach_read_web` reads a URL through Agent-Reach's Jina Reader channel.
- `agent_reach_youtube_metadata` runs `yt-dlp --dump-json --skip-download` with
  fixed arguments and bounded output.
