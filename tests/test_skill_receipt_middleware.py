from types import SimpleNamespace

from langchain_core.messages import SystemMessage

from jw.middleware.skill_receipt import SkillReceiptMiddleware


def _request():
    request = SimpleNamespace(
        state={},
        runtime=object(),
        system_message=SystemMessage(content="base system"),
    )
    request.override = lambda **kwargs: SimpleNamespace(
        state=request.state,
        runtime=request.runtime,
        system_message=kwargs.get("system_message", request.system_message),
    )
    return request


def test_skill_receipt_is_visible_to_model_and_preserves_exact_sources():
    receipt = {
        "schema_version": "jw-skill-receipt-v1",
        "agent": "solar-data",
        "skills": ["/skills/solar-cycle", "/skills/solar-cycle-forecast-validation"],
        "skill_count": 2,
        "missing": [],
        "status": "ok",
    }
    middleware = SkillReceiptMiddleware(receipt)

    modified = middleware.modify_request(_request())
    content = str(modified.system_message.content)

    assert "<skill_runtime_receipt>" in content
    assert "agent: solar-data" in content
    assert "status: ok" in content
    assert "/skills/solar-cycle-forecast-validation" in content
