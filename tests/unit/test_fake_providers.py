import json
import math

import pytest

from domesday.config import Settings
from domesday.extract.models import Extraction
from domesday.providers import ProviderError, make_embedder, make_llm
from domesday.providers.base import closed_schema
from domesday.providers.fake import FakeEmbedder, FakeLLM
from domesday.providers.rules import extract_rules, parse_date

NOW_MS = 1_760_486_400_000  # 2025-10-15T00:00:00Z


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


class TestFakeEmbedder:
    def test_deterministic_and_normalised(self):
        e1, e2 = FakeEmbedder(), FakeEmbedder()
        v1, v2 = e1.embed_one("I moved to New York"), e2.embed_one("I moved to New York")
        assert v1 == v2
        assert len(v1) == e1.dim == 256
        assert math.isclose(sum(x * x for x in v1), 1.0, rel_tol=1e-9)

    def test_lexical_similarity_orders_correctly(self):
        e = FakeEmbedder()
        q = e.embed_one("where does the user live")
        near = e.embed_one("the user lives in Berlin")
        far = e.embed_one("quarterly revenue grew by twelve percent")
        assert cos(q, near) > cos(q, far)

    def test_entity_name_variants_are_close(self):
        e = FakeEmbedder()
        near = cos(e.embed_one("Acme Corporation"), e.embed_one("Acme Corp"))
        far = cos(e.embed_one("Acme Corporation"), e.embed_one("Zurich"))
        assert near > 0.4 and far < 0.2 and near - far > 0.3

    def test_custom_dimension(self):
        assert len(FakeEmbedder(dim=64).embed(["a", "b"])[1]) == 64

    def test_empty_text_is_zero_safe(self):
        assert len(FakeEmbedder().embed_one("")) == 256


class TestFakeLLM:
    def test_extract_returns_valid_schema(self):
        out = FakeLLM().complete("sys", "ignored", task="extract",
                                 payload={"text": "I moved to Lisbon in May 2024.", "now_ms": NOW_MS})
        ext = Extraction.model_validate_json(out.text)
        assert [(s.entity, s.attribute, s.value) for s in ext.states] == [("User", "residence", "Lisbon")]
        assert ext.event_time == "2024-05-01"
        assert out.tokens_in > 0 and out.tokens_out > 0

    def test_answer_and_summarize(self):
        llm = FakeLLM()
        assert llm.complete("", "", task="answer", payload={"context": ["first", "second"]}).text == "first"
        assert "memory" in llm.complete("", "", task="answer", payload={"context": []}).text
        text = llm.complete("", "", task="summarize", payload={"texts": ["A happened. More.", "B too."]}).text
        assert text == "A happened; B too"


class TestRules:
    @pytest.mark.parametrize("text,expected", [
        ("I live in San Francisco.", [("User", "residence", "San Francisco")]),
        ("I moved from Austin to New York.", [("User", "residence", "New York")]),
        ("I'm a software engineer at Google.", [("User", "occupation", "software engineer"),
                                                ("User", "employer", "Google")]),
        ("My sister Maya lives in Paris.", [("Maya", "residence", "Paris")]),
        ("Bob's favorite color is blue.", [("Bob", "favorite_color", "blue")]),
        ("I am 34 years old.", [("User", "age", "34")]),
        ("My name is Dana Reyes.", [("User", "name", "Dana Reyes")]),
        ("Priya got married to Sam.", [("Priya", "spouse", "Sam")]),
    ])
    def test_states(self, text, expected):
        got = [(s.entity, s.attribute, s.value) for s in extract_rules(text, NOW_MS).states]
        assert got == expected

    def test_likes_are_multi_valued(self):
        states = extract_rules("I really like sushi and I love hiking.", NOW_MS).states
        assert {(s.value, s.cardinality) for s in states} == {("sushi", "many"), ("hiking", "many")}

    def test_relations_and_kinds(self):
        ext = extract_rules("Alice is my manager. I work at Acme Corp.", NOW_MS)
        assert ("Alice", "manager_of", "User") in [(r.source, r.relation_type, r.target) for r in ext.relations]
        kinds = {e.name: e.kind for e in ext.entities}
        assert kinds["Alice"] == "person" and kinds["Acme Corp"] == "organization"

    def test_importance_bands(self):
        assert extract_rules("The weather was nice.", NOW_MS).importance == 0.3
        assert extract_rules("I live in Oslo.", NOW_MS).importance == 0.5
        assert extract_rules("I moved to Oslo.", NOW_MS).importance == 0.8
        assert extract_rules("My grandfather was born in 1931.", NOW_MS).importance == 0.9

    @pytest.mark.parametrize("text,iso", [
        ("on 2024-03-05 we met", "2024-03-05"),
        ("since March 2021", "2021-03-01"),
        ("on July 4th, 2019", "2019-07-04"),
        ("back in 2015", "2015-01-01"),
        ("yesterday", "2025-10-14"),
        ("no date here", None),
    ])
    def test_dates(self, text, iso):
        from datetime import datetime, timezone
        assert parse_date(text, datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc)) == iso


class TestFactory:
    def test_fake_defaults(self):
        s = Settings()
        assert make_llm(s).name == "fake" and make_embedder(s).name == "fake"

    def test_anthropic_has_no_embeddings(self):
        with pytest.raises(ProviderError, match="no embedding API"):
            make_embedder(Settings(embed_provider="anthropic"))

    def test_unknown_provider(self):
        with pytest.raises(ProviderError):
            make_llm(Settings(llm_provider="nope"))


def test_closed_schema_marks_every_object():
    original = Extraction.model_json_schema()
    schema = closed_schema(original)
    assert schema["additionalProperties"] is False
    assert all(d.get("additionalProperties") is False for d in schema["$defs"].values() if "properties" in d)
    assert "additionalProperties" not in json.dumps(original)  # the input is not mutated
