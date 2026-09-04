from flowhub_api.services.task_lineage import historical_split_parent_ids


def test_historical_split_audits_restore_direct_child_parent_links():
    links = historical_split_parent_ids([
        {"target": "T-parent · 需求拆分", "after": {"children": ["T-child-a", "T-child-b"]}},
        {"target": "not-a-task", "after": {"children": ["ignored"]}},
    ], {"T-child-a", "T-child-b", "unrelated"})

    assert links == {"T-child-a": "T-parent", "T-child-b": "T-parent"}
