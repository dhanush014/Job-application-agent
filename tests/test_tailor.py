from pathlib import Path

from jobagent.config import load_master
from jobagent.models import TailorOutput
from jobagent.tailor import validate

ROOT = Path(__file__).resolve().parents[1]
M = load_master(ROOT / "profile.example/master_resume.yaml")


def out(bullets, skills=()):
    return TailorOutput.model_validate({"bullets": bullets, "skills": list(skills)})


def test_invented_number_is_rejected():
    t = validate(M, out([{"source_id": "stripe-3", "text": "Designed an idempotent retry framework for payment webhooks, reducing duplicate charges by 99%.", "relevance": 90}]), [])
    assert "stripe-3" not in t.texts
    assert any("invented numbers" in r for r in t.rejected)


def test_unbacked_jd_keyword_is_rejected_but_backed_one_allowed():
    bad = {"source_id": "stripe-3", "text": "Designed an idempotent retry framework on Kubernetes for payment webhooks, reducing duplicate-charge incidents by 90%.", "relevance": 90}
    good = {"source_id": "stripe-1", "text": "Built a real-time fraud detection pipeline in Go and Kafka (40K events/sec), cutting feature latency from 2s to 150ms.", "relevance": 80}
    t = validate(M, out([bad, good]), ["Kubernetes", "Kafka", "Go"])
    assert "stripe-3" not in t.texts
    assert "stripe-1" in t.texts


def test_ranking_complete_and_unknown_ids_dropped():
    t = validate(M, out([{"source_id": "nope", "text": "x", "relevance": 100}, {"source_id": "kv-1", "text": "", "relevance": 99}]), [])
    assert t.ranking[0] == "kv-1"
    assert "nope" not in t.ranking
    assert sorted(t.ranking) == sorted(b.id for b in M.iter_bullets())


def test_skills_limited_to_master_list():
    t = validate(M, out([], ["kafka", "Blockchain", "Go", "go"]), [])
    assert t.skills == ["Kafka", "Go"]
