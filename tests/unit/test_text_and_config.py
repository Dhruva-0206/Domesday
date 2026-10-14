import pytest

from domesday.config import load_settings
from domesday.db.client import graph_name
from domesday.text import attr_key, content_tokens, entity_key


@pytest.mark.parametrize("raw,key", [("The  Acme, Inc.", "the acme inc"), ("Zürich", "zurich"),
                                     ("New-York", "new york"), ("  Bob ", "bob")])
def test_entity_key(raw, key):
    assert entity_key(raw) == key


def test_attr_key():
    assert attr_key("Lives In") == "lives_in"
    assert attr_key("favourite-colour") == "favourite_colour"


def test_content_tokens_drop_stopwords_and_punctuation():
    assert content_tokens("Where do I live now?") == ["live"]
    assert content_tokens("What's Bob's job at Acme?") == ["bob", "job", "acme"]


def test_graph_name_validation():
    assert graph_name("user", "alice") == "dd:user:alice"
    for kind, ident in [("admin", "x"), ("user", "a:b"), ("user", ""), ("user", "x" * 65), ("team", "-lead")]:
        with pytest.raises(ValueError):
            graph_name(kind, ident)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("FALKORDB_PORT", "6390")
    monkeypatch.setenv("DOMESDAY_LLM_PROVIDER", "Anthropic")
    monkeypatch.setenv("FALKORDB_PASSWORD", "")
    monkeypatch.setenv("DOMESDAY_ASYNC_WRITES", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    s = load_settings(dotenv=False)
    assert s.falkordb_port == 6390 and s.llm_provider == "anthropic"
    assert s.falkordb_password is None and s.async_writes is True
    assert s.anthropic_api_key == "sk-test-secret" and "sk-test-secret" not in repr(s)
