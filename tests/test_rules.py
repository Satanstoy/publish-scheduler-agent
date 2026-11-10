from __future__ import annotations

from datetime import datetime

from conftest import NOW, new_store, single_request
from publish_agent.rules import check_rules


def test_no_risk_passes(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", time="10:00")
    report = check_rules(store.materials["M00"], request.item_for("M00"), store.rules, NOW)
    assert report.risk_level == "none"
    assert report.missing_fields == []
    assert report.risks == []


def test_missing_fields_are_hard_and_listed(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M02", time="14:00")
    report = check_rules(store.materials["M02"], request.item_for("M02"), store.rules, NOW)
    assert report.risk_level == "blocked"
    assert set(report.missing_fields) == {"duration_minutes", "metadata.copyright_owner"}
    assert all(risk["severity"] == "hard" for risk in report.risks)


def test_forbidden_term_blocks(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M03", time="15:00")
    report = check_rules(store.materials["M03"], request.item_for("M03"), store.rules, NOW)
    assert report.risk_level == "blocked"
    assert any(risk["code"] == "FORBIDDEN_TERM" for risk in report.risks)


def test_blackout_date_blocks(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", date="2026-11-13", time="10:00")
    report = check_rules(store.materials["M00"], request.item_for("M00"), store.rules, NOW)
    assert report.risk_level == "blocked"
    assert any(risk["code"] == "BLACKOUT_DATE" for risk in report.risks)


def test_unauthorized_blocks(tmp_path):
    store = new_store(tmp_path)
    material = dict(store.materials["M00"])
    material["metadata"] = dict(material["metadata"])
    material["metadata"]["authorized"] = False
    request = single_request("M00", time="10:00")
    report = check_rules(material, request.item_for("M00"), store.rules, NOW)
    assert report.risk_level == "blocked"
    assert any(risk["code"] == "COPYRIGHT_NOT_AUTHORIZED" for risk in report.risks)


def test_long_duration_is_warning(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M06", date="2026-11-15", time="10:00")
    report = check_rules(store.materials["M06"], request.item_for("M06"), store.rules, NOW)
    assert report.risk_level == "warning"
    assert any(risk["code"] == "LONG_DURATION_REVIEW" for risk in report.risks)


def test_short_lead_time_is_warning(tmp_path):
    store = new_store(tmp_path)
    request = single_request("M00", date="2026-11-10", time="10:00")
    submitted = datetime.fromisoformat("2026-11-10T09:00:00+08:00")
    report = check_rules(store.materials["M00"], request.item_for("M00"), store.rules, submitted)
    assert report.risk_level == "warning"
    assert any(risk["code"] == "SHORT_LEAD_TIME" for risk in report.risks)
