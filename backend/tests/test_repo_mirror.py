import json


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
