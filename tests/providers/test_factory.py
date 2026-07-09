import pytest

from app.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.factory import build_llm_provider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


def _settings(**over):
    base = dict(
        llm_model="test-model",
        openrouter_api_key="or-key",
        nvidia_api_key="nv-key",
        openai_api_key="oa-key",
        anthropic_api_key="an-key",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_openrouter_selected():
    p = build_llm_provider(_settings(llm_provider="openrouter"))
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "openrouter"
    assert p.default_model == "test-model"


def test_nvidia_selected():
    p = build_llm_provider(_settings(llm_provider="nvidia"))
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "nvidia"


def test_anthropic_selected():
    p = build_llm_provider(_settings(llm_provider="anthropic"))
    assert isinstance(p, AnthropicProvider)
    assert p.name == "anthropic"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_llm_provider(_settings(llm_provider="does-not-exist"))
