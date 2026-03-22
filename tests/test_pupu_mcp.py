import json

import pytest

from miso.pupu import (
    CatalogEntry,
    CatalogService,
    ConnectionTestResult,
    FieldSpec,
    FileCatalogCache,
    FileInstalledServerStore,
    ImportService,
    InMemorySecretStore,
    InstallProfile,
    InstalledServer,
    MCPRuntimeManager,
    MCPTestService,
    PupuMCPService,
    ToolPreview,
)
from miso.tools import Toolkit


class _FakeGitHubClient:
    def __init__(self, files=None, *, default_branch="main"):
        self.files = files or {}
        self.default_branch = default_branch

    def parse_repo_url(self, url):
        assert url == "https://github.com/example/demo"
        return "example", "demo", None

    def get_default_branch(self, owner, repo):
        assert owner == "example"
        assert repo == "demo"
        return self.default_branch

    def fetch_file(self, owner, repo, branch, path):
        assert owner == "example"
        assert repo == "demo"
        assert branch == self.default_branch
        return self.files.get(path)


class _FakeConnectedToolkit(Toolkit):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.disconnect_calls = 0

    def connect(self):
        self.connected = True
        self.register(self.read_file)
        self.register(self.search)
        return self

    def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1

    def read_file(self, path: str):
        """Read file contents."""
        return {"path": path}

    def search(self, query: str):
        """Search for content."""
        return {"query": query}


class _FakeListToolsFailureToolkit(_FakeConnectedToolkit):
    def list_tools(self):
        raise RuntimeError("boom")


def _fake_toolkit_factory(instance, materialized_config):
    del instance, materialized_config
    return _FakeConnectedToolkit()


def _list_tools_failure_factory(instance, materialized_config):
    del instance, materialized_config
    return _FakeListToolsFailureToolkit()


def test_import_claude_config_splits_multiple_servers_and_sanitizes_secrets():
    service = ImportService()

    payload = {
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                "env": {"ROOT": "/tmp", "GITHUB_TOKEN": "secret"},
            },
            "remote-search": {
                "url": "https://example.com/mcp",
                "transport": "streamable_http",
                "headers": {"Authorization": "Bearer secret", "X-Team": "core"},
            },
        }
    }

    draft = service.import_claude_config(json_text=json.dumps(payload))

    assert draft.source_kind == "claude"
    assert len(draft.entries) == 2

    local_entry = next(entry for entry in draft.entries if entry.display_name == "filesystem")
    local_profile = local_entry.profile_candidates[0]
    assert local_profile.runtime == "local"
    assert local_profile.transport == "stdio"
    assert local_entry.prefilled_config["env"] == {"ROOT": "/tmp"}
    assert local_entry.required_secrets == ("env.GITHUB_TOKEN",)

    remote_entry = next(entry for entry in draft.entries if entry.display_name == "remote-search")
    remote_profile = remote_entry.profile_candidates[0]
    assert remote_profile.runtime == "remote"
    assert remote_profile.transport == "streamable_http"
    assert remote_entry.prefilled_config["headers"] == {"X-Team": "core"}
    assert remote_entry.required_secrets == ("headers.Authorization",)


def test_import_github_repo_respects_priority_and_falls_back_to_manual_draft():
    prioritized_service = ImportService(
        github_client=_FakeGitHubClient(
            files={
                "mcp.json": json.dumps({"command": "uvx", "args": ["demo-mcp"]}),
                ".mcp.json": json.dumps({"command": "ignored"}),
            }
        )
    )
    prioritized = prioritized_service.import_github_repo(url="https://github.com/example/demo")

    assert prioritized.source_kind == "github"
    assert prioritized.entries[0].display_name == "Imported MCP"
    assert prioritized.entries[0].prefilled_config["command"] == "uvx"

    fallback_service = ImportService(github_client=_FakeGitHubClient(files={}))
    fallback = fallback_service.import_github_repo(url="https://github.com/example/demo")

    assert fallback.entries[0].source_kind == "github"
    assert fallback.entries[0].display_name == "demo"
    assert "falling back to manual draft" in fallback.warnings[-1].lower()


def test_catalog_service_caches_remote_payload_and_filters_by_runtime(tmp_path):
    calls = {"count": 0}
    payload = {
        "entries": [
            {
                "id": "local-fs",
                "slug": "local-fs",
                "name": "Local FS",
                "publisher": "Pupu",
                "description": "Local access",
                "icon_url": "",
                "verification": "verified",
                "source_url": "https://example.com/local-fs",
                "tags": ["filesystem"],
                "revoked": False,
                "install_profiles": [
                    {
                        "id": "stdio",
                        "label": "Local",
                        "runtime": "local",
                        "transport": "stdio",
                        "fields": [],
                        "required_secrets": [],
                        "default_values": {"command": "uvx"},
                    }
                ],
                "tool_preview": [{"name": "read_file"}],
            },
            {
                "id": "remote-search",
                "slug": "remote-search",
                "name": "Remote Search",
                "publisher": "Pupu",
                "description": "Remote access",
                "icon_url": "",
                "verification": "verified",
                "source_url": "https://example.com/remote-search",
                "tags": ["search"],
                "revoked": False,
                "install_profiles": [
                    {
                        "id": "http",
                        "label": "Remote",
                        "runtime": "remote",
                        "transport": "streamable_http",
                        "fields": [],
                        "required_secrets": [],
                        "default_values": {"url": "https://example.com/mcp"},
                    }
                ],
                "tool_preview": [{"name": "search"}],
            },
        ]
    }

    def fetcher(url, etag):
        calls["count"] += 1
        assert url == "https://catalog.example.com/mcp.json"
        if calls["count"] == 1:
            assert etag is None
            return payload, '"etag-1"', False
        assert etag == '"etag-1"'
        return None, '"etag-1"', True

    cache = FileCatalogCache(tmp_path / "catalog.json")
    service = CatalogService(
        remote_url="https://catalog.example.com/mcp.json",
        cache=cache,
        fetcher=fetcher,
    )

    local_entries = service.list_catalog(runtime="local")
    assert [entry.id for entry in local_entries] == ["local-fs"]

    service.refresh(force=True)
    assert calls["count"] == 2


def test_save_installed_server_marks_missing_secret_without_persisting_plaintext(tmp_path):
    service = PupuMCPService(
        installed_server_store=FileInstalledServerStore(tmp_path / "instances.json"),
        secret_store=InMemorySecretStore(),
    )

    draft = service.create_manual_draft(
        runtime="local",
        transport="stdio",
        name="Manual Local",
        config={"command": "demo-mcp", "env": {"API_KEY": "raw-secret"}},
    )
    entry = draft["entries"][0]
    profile = entry["profile_candidates"][0]

    installed = service.save_installed_server(
        draft_entry_id=entry["entry_id"],
        profile_id=profile["id"],
        display_name="Manual Local",
        config={"command": "demo-mcp"},
    )

    assert installed["status"] == "needs_secret"
    assert installed["normalized_config"].get("env", {}) == {}
    assert installed["required_secrets"] == ["env.API_KEY"]
    assert installed["configured_secrets"] == []


def test_test_service_validates_bad_local_config_and_list_tools_failures():
    secret_store = InMemorySecretStore()
    service = MCPTestService(secret_store=secret_store)

    bad_command = InstalledServer.create(
        display_name="Broken Local",
        source_kind="manual",
        runtime="local",
        transport="stdio",
        normalized_config={"command": "definitely-not-a-real-binary"},
        required_secrets=[],
    )
    result = service.test(bad_command)

    assert result.status == "failed"
    assert result.errors[0].code == "BINARY_NOT_FOUND"

    list_tools_service = MCPTestService(
        secret_store=secret_store,
        toolkit_factory=_list_tools_failure_factory,
    )
    good_local = InstalledServer.create(
        display_name="Good Local",
        source_kind="manual",
        runtime="local",
        transport="stdio",
        normalized_config={"command": "python"},
        required_secrets=[],
    )
    list_tools_result = list_tools_service.test(good_local)

    assert list_tools_result.status == "failed"
    assert list_tools_result.errors[0].code == "LIST_TOOLS_FAILED"


def test_catalog_install_flow_namespaces_tools_and_attaches_to_chat(tmp_path):
    catalog_entry = CatalogEntry(
        id="demo-catalog",
        slug="demo-catalog",
        name="Demo Catalog",
        publisher="Pupu",
        description="Demo MCP",
        icon_url="",
        verification="verified",
        source_url="https://example.com/demo",
        tags=("demo",),
        revoked=False,
        install_profiles=(
            InstallProfile(
                id="stdio",
                label="Local",
                runtime="local",
                transport="stdio",
                platforms=(),
                fields=(
                    FieldSpec(key="command", label="Command", required=True),
                    FieldSpec(key="env.API_TOKEN", label="env.API_TOKEN", required=True, secret=True, kind="secret"),
                ),
                required_secrets=("env.API_TOKEN",),
                default_values={"command": "python"},
            ),
        ),
        tool_preview=(ToolPreview(name="read_file"),),
    )

    service = PupuMCPService(
        catalog_service=CatalogService(seed_entries=[catalog_entry]),
        installed_server_store=FileInstalledServerStore(tmp_path / "instances.json"),
        secret_store=InMemorySecretStore(),
        test_service=MCPTestService(secret_store=InMemorySecretStore(), toolkit_factory=_fake_toolkit_factory),
        runtime_manager=MCPRuntimeManager(secret_store=InMemorySecretStore(), toolkit_factory=_fake_toolkit_factory),
    )

    # Reuse the same secret store between runtime and service.
    shared_secret_store = InMemorySecretStore()
    service = PupuMCPService(
        catalog_service=CatalogService(seed_entries=[catalog_entry]),
        installed_server_store=FileInstalledServerStore(tmp_path / "instances.json"),
        secret_store=shared_secret_store,
        test_service=MCPTestService(secret_store=shared_secret_store, toolkit_factory=_fake_toolkit_factory),
        runtime_manager=MCPRuntimeManager(secret_store=shared_secret_store, toolkit_factory=_fake_toolkit_factory),
    )

    installed = service.save_installed_server(
        draft_entry_id="demo-catalog",
        profile_id="stdio",
        display_name="Demo Catalog",
        config={"command": "python"},
        secrets={"env.API_TOKEN": "token"},
    )
    assert installed["status"] == "ready_for_review"

    test_result = service.test_installed_server(instance_id=installed["instance_id"])
    assert test_result["status"] == "success"
    assert test_result["tool_count"] == 2

    enabled = service.enable_installed_server(instance_id=installed["instance_id"])
    assert enabled["status"] == "enabled"
    assert enabled["tool_count"] == 2

    attached = service.attach_servers_to_chat(chat_id="chat-1", instance_ids=[installed["instance_id"]])
    assert attached == {"attached_instance_ids": [installed["instance_id"]]}

    toolkits = service.get_chat_toolkits("chat-1")
    assert len(toolkits) == 1
    assert sorted(toolkits[0].tools.keys()) == [
        "mcp__demo-catalog__read_file",
        "mcp__demo-catalog__search",
    ]

    disabled = service.disable_installed_server(instance_id=installed["instance_id"])
    assert disabled["status"] == "disabled"
    assert service.get_chat_toolkits("chat-1") == []


def test_enable_requires_successful_test_and_revoked_entries_stay_blocked(tmp_path):
    revoked_entry = CatalogEntry(
        id="revoked-entry",
        slug="revoked-entry",
        name="Revoked",
        publisher="Pupu",
        description="Revoked",
        icon_url="",
        verification="verified",
        source_url="https://example.com/revoked",
        tags=(),
        revoked=True,
        install_profiles=(
            InstallProfile(
                id="stdio",
                label="Local",
                runtime="local",
                transport="stdio",
                platforms=(),
                fields=(FieldSpec(key="command", label="Command", required=True),),
                required_secrets=(),
                default_values={"command": "demo-mcp"},
            ),
        ),
        tool_preview=(),
    )
    secret_store = InMemorySecretStore()
    service = PupuMCPService(
        catalog_service=CatalogService(seed_entries=[revoked_entry]),
        installed_server_store=FileInstalledServerStore(tmp_path / "instances.json"),
        secret_store=secret_store,
        test_service=MCPTestService(secret_store=secret_store, toolkit_factory=_fake_toolkit_factory),
        runtime_manager=MCPRuntimeManager(secret_store=secret_store, toolkit_factory=_fake_toolkit_factory),
    )

    draft = service.create_manual_draft(
        runtime="local",
        transport="stdio",
        name="Manual",
        config={"command": "python"},
    )
    entry = draft["entries"][0]
    profile = entry["profile_candidates"][0]
    installed = service.save_installed_server(
        draft_entry_id=entry["entry_id"],
        profile_id=profile["id"],
        display_name="Manual",
        config={"command": "python"},
    )

    with pytest.raises(ValueError, match="testInstalledServer"):
        service.enable_installed_server(instance_id=installed["instance_id"])

    revoked = service.save_installed_server(
        draft_entry_id="revoked-entry",
        profile_id="stdio",
        display_name="Revoked",
        config={"command": "demo-mcp"},
    )
    assert revoked["status"] == "revoked"
    assert service.enable_installed_server(instance_id=revoked["instance_id"])["status"] == "revoked"
