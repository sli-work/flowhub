import json

import pytest


def test_graphify_map_text_uses_symbols_and_relationships_with_a_prompt_budget(tmp_path):
    from flowhub_api.services.repo_mirror import graphify_map_text

    graph_dir = tmp_path / "graph"
    output_dir = graph_dir / "graphify-out"
    output_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text(json.dumps({"commit": "abc123456789"}), encoding="utf-8")
    (output_dir / "graph.json").write_text(json.dumps({
        "nodes": [
            {"label": "OrderService", "type": "class", "source_file": "src/order.py", "source_location": "12"},
            {"label": "create_order", "type": "function", "source_file": "src/order.py", "source_location": "33"},
        ],
        "edges": [{"source": "OrderService", "target": "create_order", "relation": "calls"}],
    }), encoding="utf-8")

    result = graphify_map_text("orders/api", "核心服务", graph_dir, max_chars=500)

    assert "Graphify 代码地图" in result
    assert "abc123456789" in result
    assert "OrderService" in result
    assert "调用关系 1" in result
    assert len(result) <= 500


def test_graphify_query_text_returns_ranked_symbols_and_commit_evidence(tmp_path):
    from flowhub_api.services.repo_mirror import graphify_query_text

    graph_dir = tmp_path / "graph"
    output_dir = graph_dir / "graphify-out"
    output_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text(json.dumps({"commit": "abcdef123456"}), encoding="utf-8")
    (output_dir / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "create_order", "name": "create_order", "type": "function", "file": "src/orders.py", "line": 12},
            {"id": "send_email", "name": "send_email", "type": "function", "file": "src/mail.py", "line": 8},
        ],
        "edges": [{"source": "create_order", "target": "send_email"}],
    }), encoding="utf-8")

    result = graphify_query_text("orders/api", "核心", graph_dir, "create_order 如何调用")

    assert "create_order" in result
    assert "src/orders.py:L12" in result
    assert "abcdef123456" in result
    assert "create_order → send_email" in result


def test_graphify_query_resolves_link_object_endpoints_and_prioritizes_exact_symbol(tmp_path):
    from flowhub_api.services.repo_mirror import graphify_query_text

    graph_dir = tmp_path / "graph"
    output_dir = graph_dir / "graphify-out"
    output_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text(json.dumps({"commit": "abcdef123456"}), encoding="utf-8")
    (output_dir / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "1", "name": "create", "type": "function", "file": "src/legacy.py", "line": 2},
            {"id": "2", "name": "create_order", "type": "function", "file": "src/orders.py", "line": 20},
            {"id": "3", "name": "notify", "type": "function", "file": "src/mail.py", "line": 7},
        ],
        "links": [{"source": {"id": "2"}, "target": {"id": "3"}, "type": "calls"}],
    }), encoding="utf-8")

    result = graphify_query_text("orders/api", "核心", graph_dir, "create_order 的调用链")

    assert result.index("create_order") < result.index("create（")
    assert "create_order → notify" in result


def test_graphify_query_falls_back_to_bounded_git_grep_when_graph_has_no_match(tmp_path, monkeypatch):
    from flowhub_api.services import repo_mirror

    graph_dir = tmp_path / "graph"
    output_dir = graph_dir / "graphify-out"
    output_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text(json.dumps({"commit": "abcdef123456"}), encoding="utf-8")
    (output_dir / "graph.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    mirror = tmp_path / "repo.git"
    mirror.mkdir()

    class Result:
        returncode = 0
        stdout = "abcdef123456:src/orders.py:18:def cancel_order(order_id):\n"
        stderr = ""

    monkeypatch.setattr(repo_mirror, "_run_git", lambda *args, **kwargs: Result())
    result = repo_mirror.graphify_query_text("orders/api", "核心", graph_dir, "cancel_order 如何实现", mirror=mirror)

    assert "文本检索证据" in result
    assert "src/orders.py:18" in result
    assert "cancel_order" in result


@pytest.mark.asyncio
async def test_ready_graphify_map_uses_only_a_current_prebuilt_graph(tmp_path, monkeypatch):
    """Interactive analysis must not build Graphify synchronously."""
    from types import SimpleNamespace
    from flowhub_api.services import repo_mirror

    mirror = tmp_path / "repo.git"
    mirror.mkdir()
    graph_dir = tmp_path / "graph"
    output_dir = graph_dir / "graphify-out"
    output_dir.mkdir(parents=True)
    (graph_dir / "metadata.json").write_text(json.dumps({"commit": "abcdef123456"}), encoding="utf-8")
    (output_dir / "graph.json").write_text(json.dumps({"nodes": [{"name": "create_order"}]}), encoding="utf-8")
    monkeypatch.setattr(repo_mirror, "_mirror_commit", lambda unused: "abcdef123456")
    monkeypatch.setattr(repo_mirror, "graphify_dir", lambda unused: graph_dir)

    result = await repo_mirror.ready_graphify_map(SimpleNamespace(id="repo-1", full_name="orders/api"), mirror)

    assert result == graph_dir


def test_repo_tool_read_returns_pinned_line_evidence_and_clamps_range(tmp_path, monkeypatch):
    from flowhub_api.services import repo_mirror

    mirror = tmp_path / "repo.git"
    mirror.mkdir()

    class Result:
        returncode = 0
        stdout = "one\ntwo\nthree\n"
        stderr = ""

    monkeypatch.setattr(repo_mirror, "_run_git", lambda *args, **kwargs: Result())

    result = repo_mirror.repo_read_file(mirror, "abcdef123456", "src/orders.py", start_line=2, end_line=999)

    assert result["tool"] == "repo_read_file"
    assert result["commit"] == "abcdef123456"
    assert result["evidence"] == "src/orders.py:L2 two\nsrc/orders.py:L3 three"
    assert result["truncated"] is False


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret.py", "src/../../secret.py", ""])
def test_repo_tools_reject_paths_outside_the_pinned_repository(tmp_path, path):
    from flowhub_api.services.repo_mirror import RepoToolError, repo_read_file

    mirror = tmp_path / "repo.git"
    mirror.mkdir()

    with pytest.raises(RepoToolError, match="路径"):
        repo_read_file(mirror, "abcdef123456", path)


def test_repo_search_uses_literal_git_arguments_and_bounds_results(tmp_path, monkeypatch):
    from flowhub_api.services import repo_mirror

    mirror = tmp_path / "repo.git"
    mirror.mkdir()
    calls = []

    class Result:
        returncode = 0
        stdout = "abcdef:src/orders.py:5:needle\nabcdef:src/orders.py:8:needle\n"
        stderr = ""

    def fake_git(args, *unused, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(repo_mirror, "_run_git", fake_git)

    result = repo_mirror.repo_search(mirror, "abcdef123456", "needle; rm -rf /", limit=999)

    assert calls[0][:4] == ["grep", "-n", "--fixed-strings", "-m"]
    assert calls[0][4] == str(repo_mirror.REPO_TOOL_MAX_SEARCH_RESULTS)
    assert calls[0][5:7] == ["-e", "needle; rm -rf /"]
    assert result["result_count"] == 2
    assert "src/orders.py:L5" in result["evidence"]


def test_repo_find_files_filters_before_applying_the_result_budget(tmp_path, monkeypatch):
    """A target after the directory-list budget must not become a false miss."""
    from flowhub_api.services import repo_mirror

    mirror = tmp_path / "repo.git"
    mirror.mkdir()
    files = [f"src/generated/file_{index}.py" for index in range(250)] + ["src/feature/target.service.ts"]

    class Result:
        returncode = 0
        stdout = "\n".join(files) + "\n"
        stderr = ""

    monkeypatch.setattr(repo_mirror, "_run_git", lambda *args, **kwargs: Result())

    result = repo_mirror.repo_find_files(mirror, "abcdef123456", "*.service.ts", limit=10)

    assert result["result_count"] == 1
    assert result["evidence"] == "src/feature/target.service.ts"
    assert result["truncated"] is False


def test_repo_search_filters_glob_before_applying_the_result_budget(tmp_path, monkeypatch):
    """Path filtering must scan beyond the first generic grep page."""
    from flowhub_api.services import repo_mirror

    mirror = tmp_path / "repo.git"
    mirror.mkdir()
    lines = [f"abcdef:src/generated/file_{index}.py:1:needle" for index in range(80)]
    lines.append("abcdef:src/feature/order.service.ts:8:needle")

    class Result:
        returncode = 0
        stdout = "\n".join(lines) + "\n"
        stderr = ""

    monkeypatch.setattr(repo_mirror, "_run_git", lambda *args, **kwargs: Result())

    result = repo_mirror.repo_search(mirror, "abcdef123456", "needle", glob="*.service.ts", limit=10)

    assert result["result_count"] == 1
    assert "src/feature/order.service.ts:L8 needle" in result["evidence"]
    assert result["truncated"] is False
