"""Checkpoint decoding with no dynamic imports, methods, or pickle revival.

FlowHub graph state contains JSON values. Only LangGraph's Interrupt and Send
control records require object reconstruction. Old checkpoints containing other
Python objects fail closed and require a fresh run, rather than importing code.
"""
import json

import ormsgpack
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Interrupt, Send


def _control_record(code: int, data: bytes):
    # Apply the same restriction recursively, including values inside control records.
    payload = ormsgpack.unpackb(data, ext_hook=_control_record, option=ormsgpack.OPT_NON_STR_KEYS)
    if not isinstance(payload, (tuple, list)) or len(payload) != 3:
        raise ValueError('Unsupported checkpoint extension payload')
    module, name, values = payload
    if code == 2 and module == 'langgraph.types' and name == 'Interrupt' and isinstance(values, dict):
        if set(values) - {'value', 'id'}:
            raise ValueError('Unsupported checkpoint interrupt fields')
        return Interrupt(**values)
    if code == 1 and module == 'langgraph.types' and name == 'Send' and isinstance(values, (tuple, list)) and len(values) == 2:
        return Send(*values)
    raise ValueError('Unsupported checkpoint object type')


class FlowHubCheckpointSerializer(JsonPlusSerializer):
    def __init__(self):
        super().__init__(pickle_fallback=False, __unpack_ext_hook__=_control_record)

    def dumps_typed(self, obj):
        try:
            return super().dumps_typed(obj)
        except (TypeError, UnicodeError):
            # ormsgpack versions disagree on their invalid UTF-8 exception type.
            # Plain JSON supports escaped surrogates and rejects Python objects.
            return 'json', self.dumps(obj)

    def dumps(self, obj):
        # Preserve lone surrogates while avoiding constructor metadata on JSON fallback.
        return json.dumps(obj, ensure_ascii=True).encode('utf-8')

    def loads(self, data):
        # Constructor-shaped user dictionaries stay dictionaries. Never use LC_REVIVER.
        return json.loads(data)
