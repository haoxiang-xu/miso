from __future__ import annotations

import builtins
import subprocess
import sys
import types


def _remove_agent_reach_modules(monkeypatch):
    for name in list(sys.modules):
        if name == "agent_reach" or name.startswith("agent_reach."):
            monkeypatch.delitem(sys.modules, name, raising=False)


def _install_fake_agent_reach(monkeypatch, *, doctor_result=None, web_content=""):
    root = types.ModuleType("agent_reach")
    root.__path__ = []

    class FakeAgentReach:
        def doctor(self):
            return doctor_result if doctor_result is not None else {}

    root.AgentReach = FakeAgentReach

    channels = types.ModuleType("agent_reach.channels")
    channels.__path__ = []

    web = types.ModuleType("agent_reach.channels.web")

    class FakeWebChannel:
        seen_urls = []

        def read(self, url):
            self.seen_urls.append(url)
            return web_content

    web.WebChannel = FakeWebChannel

    monkeypatch.setitem(sys.modules, "agent_reach", root)
    monkeypatch.setitem(sys.modules, "agent_reach.channels", channels)
    monkeypatch.setitem(sys.modules, "agent_reach.channels.web", web)
    return FakeWebChannel


def _toolkit():
    from unchain.toolkits import AgentReachToolkit

    return AgentReachToolkit()


def test_status_reports_missing_agent_reach_dependency(monkeypatch):
    _remove_agent_reach_modules(monkeypatch)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        level = args[3] if len(args) > 3 else kwargs.get("level", 0)
        if level == 0 and (name == "agent_reach" or name.startswith("agent_reach.")):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    toolkit = _toolkit()
    result = toolkit.agent_reach_status()

    assert result["ok"] is False
    assert result["missing_dependency"] == "agent-reach"
    assert "pip install agent-reach" in result["install"]


def test_status_returns_agent_reach_doctor_payload(monkeypatch):
    expected = {
        "web": {
            "status": "ok",
            "name": "Web",
            "message": "available",
            "tier": 0,
            "backends": ["Jina Reader"],
        }
    }
    _install_fake_agent_reach(monkeypatch, doctor_result=expected)

    assert _toolkit().agent_reach_status() == expected


def test_read_web_uses_agent_reach_web_channel_and_truncates(monkeypatch):
    fake_web = _install_fake_agent_reach(monkeypatch, web_content="abcdef")

    result = _toolkit().agent_reach_read_web("https://example.com", max_chars=3)

    assert fake_web.seen_urls == ["https://example.com"]
    assert result == {
        "ok": True,
        "url": "https://example.com",
        "content": "abc",
        "truncated": True,
    }


def test_youtube_metadata_uses_fixed_ytdlp_argv_and_truncates(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="abcdef", stderr="warning")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/yt-dlp" if name == "yt-dlp" else None)
    monkeypatch.setattr("subprocess.run", fake_run)

    result = _toolkit().agent_reach_youtube_metadata(
        "https://youtu.be/video",
        max_output_chars=4,
    )

    assert calls[0][0] == [
        "/usr/bin/yt-dlp",
        "--dump-json",
        "--skip-download",
        "https://youtu.be/video",
    ]
    assert calls[0][1]["shell"] is False
    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["stdout"] == "abcd"
    assert result["stderr"] == "warn"
    assert result["truncated"] is True


def test_youtube_metadata_reports_missing_ytdlp(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)

    result = _toolkit().agent_reach_youtube_metadata("https://youtu.be/video")

    assert result["ok"] is False
    assert result["missing_dependency"] == "yt-dlp"
    assert "agent-reach" in result["install"]
