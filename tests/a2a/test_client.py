from slack_agents.a2a.client import INTERRUPTED, NONTERMINAL, TERMINAL, A2AResult, classify


def test_state_buckets_are_disjoint_and_complete():
    assert TERMINAL == {"completed", "failed", "canceled", "rejected"}
    assert INTERRUPTED == {"input-required", "auth-required"}
    assert NONTERMINAL == {"submitted", "working"}
    assert TERMINAL & INTERRUPTED == set()
    assert TERMINAL & NONTERMINAL == set()


def test_classify_maps_state_to_bucket():
    assert classify("completed") == "terminal"
    assert classify("working") == "non_terminal"
    assert classify("input-required") == "interrupted"
    assert classify("message") == "terminal"
    assert classify("banana") == "terminal"


def test_a2aresult_is_a_dataclass():
    r = A2AResult(state="completed", text="hi", context_id="c1", task_id="t1")
    assert (r.state, r.text, r.context_id, r.task_id) == ("completed", "hi", "c1", "t1")
