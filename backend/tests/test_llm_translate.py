import json

from app.runner.llm import _to_openai_messages


def test_anthropic_blocks_to_openai():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"},
                                          {"type": "tool_use", "id": "t1", "name": "list_goals", "input": {"a": 1}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{\"ok\":true}"}]},
    ]
    out = _to_openai_messages("sys", msgs)
    assert out[0] == {"role": "system", "content": "sys"}
    a = out[2]
    assert a["tool_calls"][0]["function"]["name"] == "list_goals"
    assert json.loads(a["tool_calls"][0]["function"]["arguments"]) == {"a": 1}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "{\"ok\":true}"}
