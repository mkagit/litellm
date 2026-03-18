import datetime as real_datetime
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import litellm
from litellm.caching.caching import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail, ModifyResponseException
from litellm.proxy._types import ProxyErrorTypes
from litellm.types.guardrails import GuardrailEventHooks
from litellm.proxy.utils import ProxyLogging

sys.path.insert(
    0, os.path.abspath("../../..")
)  # Adds the parent directory to the system path

from litellm.proxy.utils import get_custom_url, join_paths


class CountingGuardrail(CustomGuardrail):
    def __init__(self, guardrail_name: str):
        super().__init__(
            guardrail_name=guardrail_name,
            event_hook="pre_call",
            default_on=True,
        )
        self.calls = 0

    def should_run_guardrail(self, data, event_type) -> bool:
        return True

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        self.calls += 1
        return None


def test_get_custom_url(monkeypatch):
    monkeypatch.setenv("SERVER_ROOT_PATH", "/litellm")
    custom_url = get_custom_url(request_base_url="http://0.0.0.0:4000", route="ui/")
    assert custom_url == "http://0.0.0.0:4000/litellm/ui/"


def test_proxy_only_error_true_for_llm_route():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    assert proxy_logging_obj._is_proxy_only_llm_api_error(
        original_exception=Exception(),
        error_type=ProxyErrorTypes.auth_error,
        route="/v1/chat/completions",
    )


def test_proxy_only_error_true_for_info_route():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    assert (
        proxy_logging_obj._is_proxy_only_llm_api_error(
            original_exception=Exception(),
            error_type=ProxyErrorTypes.auth_error,
            route="/key/info",
        )
        is True
    )


def test_proxy_only_error_false_for_non_llm_non_info_route():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    assert (
        proxy_logging_obj._is_proxy_only_llm_api_error(
            original_exception=Exception(),
            error_type=ProxyErrorTypes.auth_error,
            route="/key/generate",
        )
        is False
    )


def test_proxy_only_error_false_for_other_error_type():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    assert (
        proxy_logging_obj._is_proxy_only_llm_api_error(
            original_exception=Exception(),
            error_type=None,
            route="/v1/chat/completions",
        )
        is False
    )


@pytest.mark.asyncio
async def test_pre_call_hook_skips_pipeline_managed_guardrail_list():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    guardrail = CountingGuardrail(guardrail_name="content-filter")
    original_callbacks = litellm.callbacks
    litellm.callbacks = [guardrail]

    try:
        result = await proxy_logging_obj.pre_call_hook(
            user_api_key_dict=MagicMock(),
            data={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {
                    "_pipeline_managed_guardrails": ["content-filter"],
                },
            },
            call_type="completion",
        )
    finally:
        litellm.callbacks = original_callbacks

    assert result is not None
    assert guardrail.calls == 0


@pytest.mark.asyncio
async def test_post_call_failure_hook_skips_proxy_failure_logging_for_guardrail_http_exception():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    proxy_logging_obj.update_request_status = AsyncMock()
    proxy_logging_obj._handle_logging_proxy_only_error = AsyncMock()

    await proxy_logging_obj.post_call_failure_hook(
        request_data={"litellm_call_id": "guardrail-http-400"},
        original_exception=HTTPException(
            status_code=400,
            detail={"error": "Blocked by guardrail"},
        ),
        user_api_key_dict=MagicMock(request_route="/v1/chat/completions"),
    )

    proxy_logging_obj.update_request_status.assert_not_awaited()
    proxy_logging_obj._handle_logging_proxy_only_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_call_failure_hook_skips_proxy_failure_logging_for_modify_response_exception():
    proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())
    proxy_logging_obj.update_request_status = AsyncMock()
    proxy_logging_obj._handle_logging_proxy_only_error = AsyncMock()

    await proxy_logging_obj.post_call_failure_hook(
        request_data={"litellm_call_id": "guardrail-modify-response"},
        original_exception=ModifyResponseException(
            message="Blocked by guardrail",
            model="gpt-4o-mini",
            request_data={"stream": True},
            guardrail_name="pipeline:test-policy",
            detection_info=None,
        ),
        user_api_key_dict=MagicMock(request_route="/v1/chat/completions"),
    )

    proxy_logging_obj.update_request_status.assert_not_awaited()
    proxy_logging_obj._handle_logging_proxy_only_error.assert_not_awaited()


def test_handle_pipeline_result_adds_guardrail_information_for_modify_response():
    guardrail = CountingGuardrail(guardrail_name="content-filter")
    original_callbacks = litellm.callbacks
    litellm.callbacks = [guardrail]
    result = SimpleNamespace(
        terminal_action="modify_response",
        modify_response_message="Blocked by policy",
        error_message=None,
        step_results=[
            SimpleNamespace(
                guardrail_name="content-filter",
                outcome="fail",
                action_taken="modify_response",
                error_detail="Blocked by policy",
                duration_seconds=0.12,
            )
        ],
    )
    data = {"metadata": {}}

    try:
        with pytest.raises(ModifyResponseException):
            ProxyLogging._handle_pipeline_result(
                result=result,
                data=data,
                policy_name="policy-a",
                event_hook=GuardrailEventHooks.pre_call.value,
            )
    finally:
        litellm.callbacks = original_callbacks

    info = data["metadata"]["standard_logging_guardrail_information"]
    assert len(info) == 1
    assert info[0]["guardrail_name"] == "content-filter"
    assert info[0]["guardrail_status"] == "guardrail_intervened"
    assert info[0]["guardrail_mode"] == GuardrailEventHooks.pre_call.value


def test_get_model_group_info_order():
    from litellm import Router
    from litellm.proxy.proxy_server import _get_model_group_info

    router = Router(
        model_list=[
            {
                "model_name": "openai/tts-1",
                "litellm_params": {
                    "model": "openai/tts-1",
                    "api_key": "sk-1234",
                },
            },
            {
                "model_name": "openai/gpt-3.5-turbo",
                "litellm_params": {
                    "model": "openai/gpt-3.5-turbo",
                    "api_key": "sk-1234",
                },
            },
        ]
    )
    model_list = _get_model_group_info(
        llm_router=router,
        all_models_str=["openai/tts-1", "openai/gpt-3.5-turbo"],
        model_group=None,
    )

    model_groups = [m.model_group for m in model_list]
    assert model_groups == ["openai/tts-1", "openai/gpt-3.5-turbo"]


def test_join_paths_no_duplication():
    """Test that join_paths doesn't duplicate route when base_path already ends with it"""
    result = join_paths(
        base_path="http://0.0.0.0:4000/my-custom-path/", route="/my-custom-path"
    )
    assert result == "http://0.0.0.0:4000/my-custom-path"


def test_join_paths_normal_join():
    """Test normal path joining"""
    result = join_paths(base_path="http://0.0.0.0:4000", route="/api/v1")
    assert result == "http://0.0.0.0:4000/api/v1"


def test_join_paths_with_trailing_slash():
    """Test path joining with trailing slash on base_path"""
    result = join_paths(base_path="http://0.0.0.0:4000/", route="api/v1")
    assert result == "http://0.0.0.0:4000/api/v1"


def test_join_paths_empty_base():
    """Test path joining with empty base_path"""
    result = join_paths(base_path="", route="api/v1")
    assert result == "/api/v1"


def test_join_paths_empty_route():
    """Test path joining with empty route"""
    result = join_paths(base_path="http://0.0.0.0:4000", route="")
    assert result == "http://0.0.0.0:4000"


def test_join_paths_both_empty():
    """Test path joining with both empty"""
    result = join_paths(base_path="", route="")
    assert result == "/"


def test_join_paths_nested_path():
    """Test path joining with nested paths"""
    result = join_paths(base_path="http://0.0.0.0:4000/v1", route="chat/completions")
    assert result == "http://0.0.0.0:4000/v1/chat/completions"


def _patch_today(monkeypatch, year, month, day):
    class PatchedDate(real_datetime.date):
        @classmethod
        def today(cls):
            return real_datetime.date(year, month, day)

    monkeypatch.setattr("litellm.proxy.utils.date", PatchedDate)


def test_get_projected_spend_over_limit_day_one(monkeypatch):
    from litellm.proxy.utils import _get_projected_spend_over_limit

    _patch_today(monkeypatch, 2026, 1, 1)
    result = _get_projected_spend_over_limit(100.0, 1.0)

    assert result is not None
    projected_spend, projected_exceeded_date = result
    assert projected_spend == 3100.0
    assert projected_exceeded_date == real_datetime.date(2026, 1, 1)


def test_get_projected_spend_over_limit_december(monkeypatch):
    from litellm.proxy.utils import _get_projected_spend_over_limit

    _patch_today(monkeypatch, 2026, 12, 15)
    result = _get_projected_spend_over_limit(100.0, 1.0)

    assert result is not None
    projected_spend, projected_exceeded_date = result
    assert projected_spend == pytest.approx(214.28571428571428)
    assert projected_exceeded_date == real_datetime.date(2026, 12, 15)


def test_get_projected_spend_over_limit_includes_current_spend(monkeypatch):
    from litellm.proxy.utils import _get_projected_spend_over_limit

    _patch_today(monkeypatch, 2026, 4, 11)
    result = _get_projected_spend_over_limit(100.0, 200.0)

    assert result is not None
    projected_spend, projected_exceeded_date = result
    assert projected_spend == 290.0
    assert projected_exceeded_date == real_datetime.date(2026, 4, 21)
