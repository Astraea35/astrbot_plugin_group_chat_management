"""Exercise merged entry points without connecting to any live platform."""

import ast
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]


def load_class(filename, name, namespace=None):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    node.decorator_list = []
    node.bases = [] if name != "ManagementConfig" else node.bases
    for method in node.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            node,
        ],
        type_ignores=[],
    )
    env = namespace or {}
    env.setdefault("adapt_event", lambda event: event)
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), env)
    return env[name]


class NestedConfigTests(unittest.TestCase):
    def test_saving_management_settings_preserves_parent_and_other_settings(self):
        class Config(dict):
            def __setattr__(self, key, value):
                self[key] = value

            def save_config(self):
                object.__setattr__(self, "saved", dict(self))

        cls = load_class(
            "admin/config.py", "ManagementConfig", {"AstrBotConfig": Config}
        )
        parent = Config(
            management={"random_ban_time": "30~300"},
            moderation_require_ai_confirmation=True,
        )
        nested = cls(parent)
        nested["random_ban_time"] = "60~600"
        nested.save_config()
        self.assertEqual(parent["management"], {"random_ban_time": "60~600"})
        self.assertTrue(parent["moderation_require_ai_confirmation"])
        self.assertNotIn("parent", parent["management"])


class RequestFlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_approval_preserves_colon_separated_request_flag(self):
        cls = load_class(
            "admin/core/join_handle.py",
            "JoinHandle",
            {"get_reply_message_str": lambda e: e.reply},
        )
        obj = cls.__new__(cls)
        bot = types.SimpleNamespace(set_group_add_request=AsyncMock())
        event = types.SimpleNamespace(
            bot=bot,
            reply="【进群申请】批准/驳回：\n昵称：合成申请人\nQQ：10001\nflag：slreq:1:42:999:1:12345",
        )
        result = await obj.set_approve(event)
        bot.set_group_add_request.assert_awaited_once_with(
            flag="slreq:1:42:999:1:12345", sub_type="add", approve=True, reason=""
        )
        self.assertIn("已同意", result)

    async def test_malformed_quoted_message_does_not_issue_approval(self):
        cls = load_class(
            "admin/core/join_handle.py",
            "JoinHandle",
            {"get_reply_message_str": lambda e: e.reply},
        )
        obj = cls.__new__(cls)
        bot = types.SimpleNamespace(set_group_add_request=AsyncMock())
        event = types.SimpleNamespace(
            bot=bot, reply="【进群申请】\n昵称：测试\nQQ：10001\n不是标识：anything"
        )
        await obj.set_approve(event)
        bot.set_group_add_request.assert_not_awaited()


class MergedDispatchTests(unittest.IsolatedAsyncioTestCase):
    def make_main(self):
        manager = types.SimpleNamespace(
            llm_perm_block=AsyncMock(return_value="你没管理员权限")
        )
        cls = load_class(
            "main.py",
            "Main",
            {"perm_manager": manager, "PermLevel": types.SimpleNamespace(ADMIN=2)},
        )
        obj = cls.__new__(cls)
        obj._admin_join = types.SimpleNamespace(event_monitoring=AsyncMock())
        obj._admin_normal = types.SimpleNamespace(
            set_group_ban=AsyncMock(return_value="done")
        )
        return obj, manager

    async def test_model_cannot_disable_permission_check(self):
        obj, manager = self.make_main()
        result = [
            item
            async for item in obj.admin_llm_set_group_ban(
                object(), 10001, 60, need_auth=False
            )
        ]
        self.assertEqual(result, ["你没管理员权限"])
        manager.llm_perm_block.assert_awaited_once()
        obj._admin_normal.set_group_ban.assert_not_awaited()

    async def test_legacy_review_does_not_duplicate_active_ai_join_review(self):
        obj, _ = self.make_main()
        obj.config = {"disclaimer_agreed": True}
        obj._get_raw_event = lambda e: {"post_type": "request", "group_id": "10000"}
        obj._cfg = lambda *a, **kw: True
        obj._check_group_access = lambda e: (True, "")
        obj._resolve_join_rule = lambda g: {"enabled": True}
        await obj.admin_event_monitoring(object())
        obj._admin_join.event_monitoring.assert_not_awaited()

    async def test_disabled_ai_review_keeps_legacy_review_available(self):
        obj, _ = self.make_main()
        obj.config = {"disclaimer_agreed": True}
        obj._get_raw_event = lambda e: {"post_type": "request", "group_id": "10000"}
        obj._cfg = lambda *a, **kw: False
        event = object()
        await obj.admin_event_monitoring(event)
        obj._admin_join.event_monitoring.assert_awaited_once_with(event)

    async def test_membership_notices_keep_welcome_and_leave_processing(self):
        obj, _ = self.make_main()
        obj._get_raw_event = lambda e: {
            "post_type": "notice",
            "notice_type": "group_increase",
        }
        event = object()
        await obj.admin_event_monitoring(event)
        obj._admin_join.event_monitoring.assert_awaited_once_with(event)

    async def test_ban_command_keeps_seconds_argument(self):
        obj, _ = self.make_main()
        event = types.SimpleNamespace(plain_result=lambda text: text)
        result = [x async for x in obj.admin_set_group_ban(event, "60")]
        obj._admin_normal.set_group_ban.assert_awaited_once_with(event, "60")
        self.assertEqual(result, ["done"])

    async def test_current_group_admin_cannot_change_another_groups_settings(self):
        obj, _ = self.make_main()
        obj._admin_db = types.SimpleNamespace(import_cn_lines=AsyncMock())
        event = types.SimpleNamespace(
            message_str="群管配置 20000 进群审核 开",
            get_group_id=lambda: "10000",
            is_admin=lambda: False,
            plain_result=lambda text: text,
        )
        obj.admin_set_config.__globals__["re"] = __import__("re")
        result = [x async for x in obj.admin_set_config(event)]
        self.assertIn("跨群", result[0])
        obj._admin_db.import_cn_lines.assert_not_awaited()

    async def test_current_group_admin_cannot_reset_another_groups_settings(self):
        obj, _ = self.make_main()
        obj._admin_db = types.SimpleNamespace(reset_to_default=AsyncMock())
        event = types.SimpleNamespace(
            get_group_id=lambda: "10000",
            is_admin=lambda: False,
            plain_result=lambda text: text,
        )
        result = [x async for x in obj.admin_reset_config(event, "20000")]
        self.assertIn("跨群", result[0])
        obj._admin_db.reset_to_default.assert_not_awaited()


class RegistrationTests(unittest.TestCase):
    def test_command_and_alias_names_are_not_registered_twice(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        names = {}
        for node in ast.walk(tree):
            for dec in getattr(node, "decorator_list", []):
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "command"
                ):
                    group = {ast.literal_eval(dec.args[0])}
                    for kw in dec.keywords:
                        if kw.arg == "alias":
                            group.update(ast.literal_eval(kw.value))
                    for name in group:
                        self.assertNotIn(name, names, f"Duplicate command: {name}")
                        names[name] = node.name
        self.assertEqual(names["禁言"], "admin_set_group_ban")
        self.assertEqual(names["批准"], "admin_agree_add_group")
        self.assertGreaterEqual(len(names), 62)


class BlacklistMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_management_blacklist_is_applied_before_ai_or_empty_approval(self):
        from test_membership_api_result import _Harness, _Client
        from test_group_review_policy import _NoticeEvent

        harness = _Harness()
        harness.config["join_accept_keywords"] = []
        harness.cfg_values.update(join_empty_auto_accept=True, join_min_qq_level=30)
        harness._admin_db = types.SimpleNamespace(get=AsyncMock(return_value=["42"]))
        client = _Client({"status": "ok", "retcode": 0})
        event = _NoticeEvent(client)
        event.raw_event["user_id"] = 42
        event.raw_event["comment"] = ""
        self.assertTrue(await harness._handle_group_request(event))
        self.assertEqual(harness.llm_calls, [])
        self.assertIs(client.calls[0][1]["approve"], False)


if __name__ == "__main__":
    unittest.main()
