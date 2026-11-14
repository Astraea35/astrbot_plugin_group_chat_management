"""AI management authorization, target binding and provider failover regressions."""

import ast
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_issue92_qq_official import _load_module, onebot

ai = _load_module(onebot.__package__ + ".ai_management", "ai_management.py")
ROOT = Path(__file__).resolve().parents[1]


class At:
    def __init__(self, qq):
        self.qq = qq


class Reply:
    def __init__(self, sender_id="member", message_id="quoted"):
        self.sender_id = sender_id
        self.id = message_id
        self.chain = []


class ToolSet:
    def __init__(self, tools):
        self.tools = tools

    def empty(self):
        return not self.tools


class Event:
    message_str = "把他禁言十分钟"
    unified_msg_origin = "platform:GroupMessage:group"

    def __init__(self, mentions=("bot", "member"), platform="aiocqhttp"):
        self.messages = [At(value) for value in mentions]
        self.platform = platform
        self.message_obj = types.SimpleNamespace(raw_message={})
        self.extras = {}
        self.stopped = False

    def get_self_id(self):
        return "bot"

    def get_group_id(self):
        return "group"

    def get_sender_id(self):
        return "operator"

    def get_platform_name(self):
        return self.platform

    def get_messages(self):
        return self.messages

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def plain_result(self, text):
        return text

    def stop_event(self):
        self.stopped = True


class Host(ai.AiManagementMixin):
    def __init__(self):
        self.config = {"disclaimer_agreed": True}
        self.admin = True
        self.allowed = True
        self.calls = []
        self._is_admin = AsyncMock(side_effect=lambda event: self.admin)
        own_tool = types.SimpleNamespace(
            name="llm_set_group_ban",
            active=True,
            handler_module_path=type(self).__module__,
        )
        foreign_tool = types.SimpleNamespace(
            name="llm_set_group_kick",
            active=True,
            handler_module_path="another.plugin",
        )
        self.context = types.SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="session-default"),
            get_llm_tool_manager=lambda: types.SimpleNamespace(
                func_list=[own_tool, foreign_tool]
            ),
            tool_loop_agent=AsyncMock(
                return_value=types.SimpleNamespace(completion_text="请指定目标")
            ),
        )

    def _cfg(self, key, default=True):
        return self.config.get(key, default)

    def _cfg_str(self, key, default=""):
        return self.config.get(key, default)

    def _get_group_id(self, event):
        return event.get_group_id()

    def _onebot_unsupported_message(self, event):
        return "" if event.platform in {"aiocqhttp", "qq_official"} else "unsupported"

    def _check_group_access(self, event):
        return self.allowed, "group denied"

    @ai.ai_management_tool
    async def ban(self, event, user_id, duration=600, need_auth=True):
        self.calls.append((user_id, duration))
        yield "synthetic mute succeeded"

    @ai.ai_management_tool
    async def recall(self, event, message_id):
        self.calls.append(message_id)
        yield "synthetic recall succeeded"


class AiManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        module = types.ModuleType("astrbot.core.agent.tool")
        module.ToolSet = ToolSet
        self.modules = patch.dict(sys.modules, {module.__name__: module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.host = Host()
        self.event = Event()

    async def run_handler(self):
        return [item async for item in self.host._handle_ai_management(self.event)]

    async def test_only_mentioning_someone_else_never_calls_model_or_action(self):
        self.event = Event(mentions=("member",))
        self.assertEqual(await self.run_handler(), [])
        self.host.context.tool_loop_agent.assert_not_awaited()
        self.host._is_admin.assert_not_awaited()
        result = [x async for x in self.host.ban(self.event, "member")]
        self.assertIn("@", result[0])
        self.assertEqual(self.host.calls, [])

    async def test_non_admin_cannot_authorize_tools_even_with_need_auth_false(self):
        self.host.admin = False
        self.assertEqual(await self.run_handler(), [])
        self.host.context.tool_loop_agent.assert_not_awaited()
        result = [x async for x in self.host.ban(self.event, "member", need_auth=False)]
        self.assertIn("仅管理员", result[0])
        self.assertEqual(self.host.calls, [])

    async def test_private_message_and_wake_flag_are_not_authorization(self):
        self.event.get_group_id = lambda: ""
        self.event.is_at_or_wake_command = True
        self.assertTrue(await self.host._ai_management_access_error(self.event))
        self.event = Event(mentions=())
        self.event.is_at_or_wake_command = True
        self.assertFalse(ai.directly_mentions_bot(self.event))

    def test_quoted_forwarded_and_textual_at_are_not_direct_mentions(self):
        self.event = Event(mentions=())
        quote = Reply(sender_id="bot")
        quote.chain = [At("bot")]
        self.event.messages = [
            quote,
            {"type": "forward", "data": {"content": [At("bot")]}},
        ]
        self.event.message_str = "@bot 禁言他"
        self.assertFalse(ai.directly_mentions_bot(self.event))

    def test_onebot_structured_dictionary_mention_is_accepted(self):
        self.event.messages = [{"type": "at", "data": {"qq": "bot"}}]
        self.assertTrue(ai.directly_mentions_bot(self.event))

    def test_official_explicit_metadata_overrides_synthetic_at(self):
        self.event.platform = "qq_official"
        self.event.message_obj.raw_message = {"mentions": []}
        self.assertFalse(ai.directly_mentions_bot(self.event))
        self.event.message_obj.raw_message = {
            "mentions": [{"id": "bot", "is_you": True}]
        }
        self.assertTrue(ai.directly_mentions_bot(self.event))

    async def test_uses_current_session_default_provider_and_only_own_tools(self):
        await self.run_handler()
        self.host.context.get_current_chat_provider_id.assert_awaited_once_with(
            self.event.unified_msg_origin
        )
        call = self.host.context.tool_loop_agent.call_args.kwargs
        self.assertEqual(call["chat_provider_id"], "session-default")
        self.assertIn('结构化消息已核实）：["member"]', call["system_prompt"])
        self.assertEqual(
            [tool.name for tool in call["tools"].tools], ["llm_set_group_ban"]
        )

    async def test_selected_provider_does_not_resolve_global_default(self):
        self.host.config["ai_management_provider_id"] = "chosen"
        await self.run_handler()
        self.host.context.get_current_chat_provider_id.assert_not_awaited()
        self.assertEqual(
            self.host.context.tool_loop_agent.call_args.kwargs["chat_provider_id"],
            "chosen",
        )

    async def test_falls_back_before_any_action(self):
        self.host.config.update(
            ai_management_provider_id="primary",
            ai_management_fallback_provider_id="backup",
        )
        self.host.context.tool_loop_agent.side_effect = [
            ConnectionError(),
            types.SimpleNamespace(completion_text="请指定目标"),
        ]
        await self.run_handler()
        calls = self.host.context.tool_loop_agent.call_args_list
        self.assertEqual(
            [c.kwargs["chat_provider_id"] for c in calls], ["primary", "backup"]
        )
        self.assertEqual(self.host.calls, [])

    async def test_default_resolution_failure_can_use_configured_backup(self):
        self.host.config["ai_management_fallback_provider_id"] = "backup"
        self.host.context.get_current_chat_provider_id.side_effect = RuntimeError()
        await self.run_handler()
        self.assertEqual(
            self.host.context.tool_loop_agent.call_args.kwargs["chat_provider_id"],
            "backup",
        )

    async def test_no_backup_does_not_try_arbitrary_providers(self):
        self.host.context.tool_loop_agent.side_effect = ConnectionError()
        result = await self.run_handler()
        self.assertIn("未执行操作", result[0])
        self.host.context.tool_loop_agent.assert_awaited_once()

    async def test_identical_primary_and_backup_are_not_retried(self):
        self.host.config.update(
            ai_management_provider_id="same", ai_management_fallback_provider_id="same"
        )
        self.host.context.tool_loop_agent.side_effect = ConnectionError()
        await self.run_handler()
        self.host.context.tool_loop_agent.assert_awaited_once()

    async def test_no_failover_after_an_action_even_if_final_model_call_fails(self):
        self.host.config["ai_management_fallback_provider_id"] = "backup"

        async def agent(**kwargs):
            async for _ in self.host.ban(self.event, "member"):
                pass
            raise ConnectionError("response lost after write")

        self.host.context.tool_loop_agent.side_effect = agent
        result = await self.run_handler()
        self.assertEqual(result, ["synthetic mute succeeded"])
        self.assertEqual(self.host.calls, [("member", 600)])
        self.host.context.tool_loop_agent.assert_awaited_once()

    async def test_duplicate_tool_call_does_not_duplicate_punishment(self):
        for _ in range(2):
            _ = [item async for item in self.host.ban(self.event, "member")]
        self.assertEqual(self.host.calls, [("member", 600)])

    async def test_duration_default_is_deterministic_and_invalid_values_are_rejected(
        self,
    ):
        for duration in (-1, True, "600", 2592001):
            result = [x async for x in self.host.ban(self.event, "member", duration)]
            self.assertIn("未执行", result[0])
        self.assertEqual(self.host.calls, [])
        _ = [x async for x in self.host.ban(self.event, "member", None)]
        self.assertEqual(self.host.calls, [("member", 600)])

    async def test_rejects_hallucinated_user_and_bot_target(self):
        for target in ("unrelated", "bot"):
            result = [item async for item in self.host.ban(self.event, target)]
            self.assertIn("未执行", result[0])
        self.assertEqual(self.host.calls, [])

    async def test_reply_can_supply_target_but_not_authorization(self):
        self.event.messages = [At("bot"), Reply(sender_id="member")]
        result = [item async for item in self.host.ban(self.event, "member")]
        self.assertEqual(result, ["synthetic mute succeeded"])
        self.event.messages = [Reply(sender_id="bot")]
        self.assertFalse(ai.directly_mentions_bot(self.event))

    async def test_recall_requires_exact_quoted_message_id(self):
        self.event.messages.append(Reply())
        result = [item async for item in self.host.recall(self.event, "other-message")]
        self.assertIn("请引用", result[0])
        self.assertEqual(self.host.calls, [])
        _ = [item async for item in self.host.recall(self.event, "quoted")]
        self.assertEqual(self.host.calls, ["quoted"])

    async def test_group_and_feature_switches_are_enforced(self):
        self.host.allowed = False
        self.assertEqual(await self.run_handler(), [])
        self.host.allowed = True
        self.host.config["ai_management_enabled"] = False
        self.assertEqual(await self.run_handler(), [])
        self.host.context.tool_loop_agent.assert_not_awaited()

    async def test_manual_commands_keep_their_original_handlers(self):
        self.event.extras["handlers_parsed_params"] = {
            "plugin.admin_set_group_ban": {"ban_time": "60"}
        }
        self.assertEqual(await self.run_handler(), [])
        self.host.context.tool_loop_agent.assert_not_awaited()

    async def test_ordinary_chat_is_not_intercepted(self):
        self.event.message_str = "今天服务器卡吗"
        self.assertEqual(await self.run_handler(), [])
        self.host.context.tool_loop_agent.assert_not_awaited()

    async def test_natural_ban_duration_does_not_use_random_manual_default(self):
        self.event.extras["handlers_parsed_params"] = {
            "plugin_admin_set_group_ban": {"ban_time": "十分钟"},
        }
        await self.run_handler()
        self.host.context.tool_loop_agent.assert_awaited_once()

    async def test_empty_primary_response_uses_backup(self):
        self.host.config["ai_management_fallback_provider_id"] = "backup"
        self.host.context.tool_loop_agent.side_effect = [
            types.SimpleNamespace(completion_text=""),
            types.SimpleNamespace(completion_text="请指定目标"),
        ]
        await self.run_handler()
        self.assertEqual(self.host.context.tool_loop_agent.await_count, 2)

    def test_every_registered_tool_has_execution_guard(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            decorators = getattr(node, "decorator_list", [])
            if any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "llm_tool"
                for d in decorators
            ):
                self.assertTrue(
                    any(
                        isinstance(d, ast.Name) and d.id == "ai_management_tool"
                        for d in decorators
                    ),
                    node.name,
                )


if __name__ == "__main__":
    unittest.main()
