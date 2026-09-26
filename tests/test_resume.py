from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jobagent.config import load_master
from jobagent.models import MasterResume
from jobagent.resume.render import bold_segments, build_doc, fit, render

ROOT = Path(__file__).resolve().parents[1]


def master_dict():
    return yaml.safe_load((ROOT / "profile.example/master_resume.yaml").read_text())


def test_example_master_is_one_clean_full_page():
    m = load_master(ROOT / "profile.example/master_resume.yaml")
    r = fit(m, [b.id for b in m.iter_bullets()])
    assert r.render.pages == 1
    assert r.warnings == []
    assert r.natural_fill >= 0.95  # no big empty band at the bottom
    assert not r.render.dangling() and r.render.lines_ok


def test_renderer_sees_new_content_every_time():
    m = load_master(ROOT / "profile.example/master_resume.yaml")
    a = render(build_doc(m, ["stripe-1"]))
    b = render(build_doc(m, ["airbnb-1"]))
    assert "fraud" in a.text and "fraud" not in b.text


def test_sparse_resume_is_not_stretched_into_huge_gaps():
    d = master_dict()
    d["roles"][0]["bullets"] = d["roles"][0]["bullets"][:3]
    d["roles"][1]["bullets"] = d["roles"][1]["bullets"][:2]
    d["projects"] = []
    m = MasterResume.model_validate(d)
    r = fit(m, [b.id for b in m.iter_bullets()])
    assert r.render.pages == 1
    assert not r.doc.layout.stretch
    assert r.doc.layout.spacing == 1.0  # used all the spacing it is allowed
    assert any("add more bullets" in w for w in r.warnings)


def test_overfull_master_is_trimmed_to_one_page_keeping_top_ranked():
    d = master_dict()
    for n in range(25):  # way more content than fits
        d["roles"][1]["bullets"].append({
            "id": f"extra-{n}",
            "text": f"Delivered internal tooling improvement number {n} that saved the team several hours every week.",
        })
    m = MasterResume.model_validate(d)
    ranking = ["extra-0", "stripe-3"] + [b.id for b in m.iter_bullets()]
    r = fit(m, ranking)
    assert r.render.pages == 1
    assert r.dropped, "something had to be dropped"
    assert "extra-0" in r.included and "stripe-3" in r.included
    # every role keeps its minimum bullets
    for role, entry in zip(m.roles, r.doc.roles):
        assert len(entry.bullets) >= role.min_bullets
    assert r.render.fill > 0.9


def test_markup_characters_are_rendered_literally():
    d = master_dict()
    d["roles"][0]["bullets"][0]["text"] = "Shipped #hashtags, $dollars, *stars*, _under_ and <angle> @refs = 100% literal text in C++ and C#."
    m = MasterResume.model_validate(d)
    r = fit(m, [b.id for b in m.iter_bullets()])
    assert r.render.pages == 1
    assert "hashtags" in r.render.text and "C#" in r.render.text


def test_schema_rejects_bad_master():
    d = master_dict()
    d["roles"][1]["bullets"][0]["id"] = "stripe-1"  # duplicate id
    with pytest.raises(ValidationError):
        MasterResume.model_validate(d)
    d = master_dict()
    d["roles"][0]["bullets"][0]["text"] = "x" * 250  # too long for two lines
    with pytest.raises(ValidationError):
        MasterResume.model_validate(d)
    d = master_dict()
    d["surprise"] = 1  # unknown keys are errors, not silently ignored
    with pytest.raises(ValidationError):
        MasterResume.model_validate(d)


def test_dangling_rewrite_is_reverted():
    m = load_master(ROOT / "profile.example/master_resume.yaml")
    src = m.bullet_index()["stripe-2"].text
    # pad so the rewrite wraps with a one-word last line
    dangling = src.rstrip(".") + " during the rollout."
    r = fit(m, [b.id for b in m.iter_bullets()], texts={"stripe-2": dangling})
    assert r.render.pages == 1
    bullets = [b for e in r.doc.roles for b in e.bullets]
    assert dangling not in bullets and src in bullets
    assert any("reverted 1 rewrite" in w for w in r.warnings)


def test_bold_segments_keep_spaces_and_respect_case():
    segs = bold_segments("Built it in Go and Kafka, then go live", ["Go", "Kafka", "Python"])
    assert "".join(x.t for x in segs) == "Built it in Go and Kafka, then go live"
    assert [x.t for x in segs if x.b] == ["Go", "Kafka"]  # the verb "go" stays plain
    assert len([x for x in bold_segments("Go Kafka Rust", ["Go", "Kafka", "Rust"]) if x.b]) == 2  # max two


def test_bold_skills_render_with_spaces_and_prefer_job_keywords():
    m = load_master(ROOT / "profile.example/master_resume.yaml")
    r = fit(m, [b.id for b in m.iter_bullets()], bold_priority=["Kafka"])
    text = " ".join(r.render.text.split())
    assert "in Go and Kafka (40K" in text
    stripe1 = r.doc.roles[0].segments[r.doc.roles[0].ids.index("stripe-1")]
    assert [x.t for x in stripe1 if x.b] == ["Kafka"]  # only the job's keyword, not Go
    assert r.render.pages == 1 and not r.render.dangling()
