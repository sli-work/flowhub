"""Checkpoint payloads may contain user-controlled JSON, never Python commands."""
import json
from unittest.mock import Mock

import ormsgpack
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, Interrupt, Send, interrupt

from flowhub_api.services.checkpoint_serde import FlowHubCheckpointSerializer


def test_json_constructor_payload_is_inert_even_after_unicode_fallback(monkeypatch):
    command = Mock()
    monkeypatch.setattr('os.system', command)
    payload = {'user': {'lc': 2, 'type': 'constructor', 'id': ['os', 'system'],
                        'args': ['untrusted']}, 'text': '\ud800'}
    serde = FlowHubCheckpointSerializer()
    encoded = serde.dumps_typed(payload)
    assert serde.loads_typed(encoded) == payload
    assert serde.loads(json.dumps(payload).encode()) == payload
    command.assert_not_called()


@pytest.mark.parametrize('code', range(7))
def test_msgpack_rejects_arbitrary_constructor_extensions(code, monkeypatch):
    command = Mock()
    monkeypatch.setattr('os.system', command)
    data = ormsgpack.packb(ormsgpack.Ext(code, ormsgpack.packb(('os', 'system', 'untrusted'))))
    with pytest.raises(ValueError):
        FlowHubCheckpointSerializer().loads_typed(('msgpack', data))
    command.assert_not_called()


def test_nested_msgpack_extensions_cannot_bypass_allowlist(monkeypatch):
    command = Mock()
    monkeypatch.setattr('os.system', command)
    nested = ormsgpack.Ext(0, ormsgpack.packb(('os', 'system', 'untrusted')))
    data = ormsgpack.packb(ormsgpack.Ext(2, ormsgpack.packb(
        ('langgraph.types', 'Interrupt', {'value': nested, 'id': 'approval'}))))
    with pytest.raises(ValueError):
        FlowHubCheckpointSerializer().loads_typed(('msgpack', data))
    command.assert_not_called()


def test_control_objects_round_trip_and_pickle_is_disabled():
    serde = FlowHubCheckpointSerializer()
    restored = serde.loads_typed(serde.dumps_typed([Interrupt(value={'id': 'i'}, id='approval'), Send('next', {'ok': True})]))
    assert restored[0].value == {'id': 'i'}
    assert restored[0].id == 'approval'
    assert restored[1] == Send('next', {'ok': True})
    with pytest.raises(NotImplementedError):
        serde.loads_typed(('pickle', b'untrusted'))


def test_real_graph_checkpoint_interrupt_and_resume():
    def approval(state):
        answer = interrupt({'toolInvocationId': state['id']})
        return {**state, 'approved': answer}
    graph = StateGraph(dict)
    graph.add_node('approval', approval)
    graph.add_edge(START, 'approval')
    graph.add_edge('approval', END)
    app = graph.compile(checkpointer=InMemorySaver(serde=FlowHubCheckpointSerializer()))
    config = {'configurable': {'thread_id': 'safe-approval'}}
    result = app.invoke({'id': 'invocation'}, config)
    assert result['__interrupt__'][0].value == {'toolInvocationId': 'invocation'}
    assert app.invoke(Command(resume=True), config)['approved'] is True
