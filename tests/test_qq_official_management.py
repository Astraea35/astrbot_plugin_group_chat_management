"""Official management regressions with synthetic IDs and no network access."""

import ast
import sys
import types
import unittest
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_issue92_qq_official import _Host as BaseHost, _Event, onebot, utilities

ROOT = Path(__file__).resolve().parents[1]
qq = sys.modules[onebot.__package__ + ".qq_official"]
GROUP, MEMBER, BOT = "SYNTHETIC_GROUP", "SYNTHETIC_MEMBER", "SYNTHETIC_BOT"


class _Host(BaseHost, utilities.UtilitiesMixin):
    pass


class Route:
    is_sandbox = False

    def __init__(self, method, path):
        self.method, self.path = method, path

    @property
    def url(self):
        return "https://example.invalid" + self.path


def event(bot=None, text="", platform="qq_official"):
    obj = _Event(
        platform=platform, sender=MEMBER, group=GROUP, bot=bot, message_str=text
    )
    obj.get_self_id = lambda: BOT
    obj.get_messages = lambda: []
    obj.is_admin = lambda: False
    obj.is_private_chat = lambda: False
    obj.stop_event = lambda: None
    obj.platform_meta = types.SimpleNamespace(name=platform)
    obj.message_obj = types.SimpleNamespace(
        raw_message={}, message_id="SYNTHETIC_MESSAGE"
    )
    return obj


def permissions():
    tree = ast.parse((ROOT / "admin/permission.py").read_text(encoding="utf-8"))
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name in {"PermLevel", "PermissionManager"}
    ]
    env = {
        "IntEnum": IntEnum,
        "is_official": qq.is_official,
        "adapt_event": qq.adapt_event,
        "OfficialAPIError": qq.OfficialAPIError,
    }
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), "admin/permission.py", "exec"), env)
    manager = env["PermissionManager"]()
    manager.cfg = types.SimpleNamespace(
        admins_id=[MEMBER], perms={}, level_threshold=50
    )
    return manager, env["PermLevel"]


class OfficialManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        http = types.ModuleType("botpy.http")
        http.Route = Route
        self.patch = patch.dict(sys.modules, {"botpy.http": http})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.request = AsyncMock(return_value={})
        self.bot = types.SimpleNamespace(
            api=types.SimpleNamespace(_http=types.SimpleNamespace(request=self.request))
        )
        self.client = qq.OfficialClient(self.bot, GROUP, BOT)

    async def test_mute_and_unmute_preserve_openids(self):
        await self.client.call_action(
            "set_group_ban", group_id=GROUP, user_id=MEMBER, duration=30
        )
        route = self.request.call_args.args[0]
        payload = self.request.call_args.kwargs["json"]["members"][0]
        self.assertEqual(
            (route.method, route.path),
            ("POST", f"/v2/groups/{GROUP}/restrict_chat_setting"),
        )
        self.assertEqual(payload["member_openid"], MEMBER)
        self.assertEqual(payload["op"], "add")
        self.assertGreater(
            datetime.fromisoformat(payload["mute_expire_at"]).timestamp(),
            datetime.now(timezone.utc).timestamp(),
        )
        await self.client.call_action("set_group_ban", user_id=MEMBER, duration=0)
        self.assertEqual(
            self.request.call_args.kwargs["json"],
            {"members": [{"op": "del", "member_openid": MEMBER, "mute_expire_at": ""}]},
        )

    async def test_numeric_qq_and_invalid_duration_never_reach_api(self):
        for uid, duration in [("12345", 30), (MEMBER, -1), (MEMBER, 30 * 86400 + 1)]:
            with self.assertRaises(qq.OfficialAPIError):
                await self.client.call_action(
                    "set_group_ban", user_id=uid, duration=duration
                )
        self.request.assert_not_awaited()

    async def test_timeout_return_none_is_not_success(self):
        self.request.return_value = None
        with self.assertRaises(qq.OfficialAPIError):
            await self.client.call_action("delete_msg", message_id="SYNTHETIC_MESSAGE")

    async def test_successful_empty_http_204_is_distinct_from_timeout(self):
        class Response:
            status = 204
            read = AsyncMock(return_value=b"")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        response = Response()
        self.bot.api._http = types.SimpleNamespace(
            check_session=AsyncMock(),
            is_sandbox=False,
            _headers={},
            _session=types.SimpleNamespace(request=lambda **kwargs: response),
        )
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.ClientTimeout = lambda **kwargs: kwargs
        with patch.dict(sys.modules, {"aiohttp": aiohttp}):
            self.assertEqual(
                await self.client.delete_msg(message_id="SYNTHETIC_MESSAGE"), {}
            )

    async def test_recall_preserves_official_message_id(self):
        await self.client.call_action("delete_msg", message_id="ROBOT1.0_SYNTHETIC")
        route = self.request.call_args.args[0]
        self.assertEqual(
            (route.method, route.path),
            ("DELETE", f"/v2/groups/{GROUP}/messages/ROBOT1.0_SYNTHETIC"),
        )

    async def test_permission_error_is_clear_bounded_and_sanitized(self):
        self.request.side_effect = RuntimeError("11253 credential=synthetic-secret")
        for _ in range(2):
            with self.assertRaises(qq.OfficialAPIError) as caught:
                await self.client.get_group_member_info(user_id=MEMBER)
            self.assertIn("11253", str(caught.exception))
            self.assertNotIn("synthetic-secret", str(caught.exception))
        self.request.assert_awaited_once()

    async def test_member_role_and_bot_role_use_separate_endpoints(self):
        self.request.return_value = {"member_role": "admin", "username": "Synthetic"}
        info = await self.client.get_group_member_info(user_id=MEMBER)
        self.assertEqual(info["role"], "admin")
        self.assertNotIn("card", info)
        await self.client.get_group_member_info(user_id=BOT)
        self.assertEqual(
            self.request.call_args.args[0].path, f"/v2/groups/{GROUP}/bot_state"
        )

    async def test_unsupported_card_and_whole_mute_never_guess_endpoint(self):
        for action in [
            "set_group_card",
            "set_group_special_title",
            "set_group_whole_ban",
            "get_stranger_info",
        ]:
            with self.assertRaisesRegex(qq.OfficialAPIError, "未提供"):
                await self.client.call_action(action, user_id=MEMBER)
        self.request.assert_not_awaited()

    async def test_approval_preserves_long_request_id_and_group_scope(self):
        request_id = "SYNTHETIC:request/with+opaque=bytes"
        flag = qq.request_flag(GROUP, MEMBER, request_id)
        await self.client.set_group_add_request(flag=flag, approve=True)
        self.assertEqual(
            self.request.call_args.kwargs["json"],
            {"op": "approve", "join_request_id": request_id},
        )
        await self.client.set_group_add_request(
            flag=flag, approve=False, reason="Synthetic rejection"
        )
        self.assertEqual(
            self.request.call_args.kwargs["json"]["reject_reason"],
            "Synthetic rejection",
        )
        for invalid in [
            "slreq:1:2:3",
            "qqofficial:broken",
            qq.request_flag("OTHER_GROUP", MEMBER, request_id),
        ]:
            with self.assertRaises(qq.OfficialAPIError):
                await self.client.set_group_add_request(flag=invalid, approve=True)
        self.assertEqual(self.request.await_count, 2)

    async def test_no_fallback_from_official_to_cached_onebot(self):
        host = _Host()
        host._client = types.SimpleNamespace(call_action=AsyncMock())
        self.assertIsNone(await host._get_client(event(bot=object())))
        self.assertIsInstance(
            await host._get_client(event(bot=self.bot)), qq.OfficialClient
        )
        host._client.call_action.assert_not_awaited()

    async def test_two_official_apps_require_observed_route(self):
        host = _Host()
        platform = lambda: types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(name="qq_official")
        )
        host.context.platform_manager = types.SimpleNamespace(
            get_insts=lambda: [platform(), platform()]
        )
        self.assertIsNone(await host._client_for_group(GROUP))

    async def test_shared_auto_mute_and_recall_paths(self):
        host = _Host()
        evt = event(bot=self.bot)
        self.assertTrue(await host._mute_member(evt, 30))
        self.assertTrue(await host._recall_msg(evt, "SYNTHETIC_MESSAGE"))
        self.assertTrue(await host._unban_member(GROUP, MEMBER))
        self.assertEqual(self.request.await_count, 3)

    async def test_sender_admin_does_not_grant_target_or_bot_permission(self):
        manager, level = permissions()
        evt = qq.OfficialEvent(event(bot=self.bot), self.client)
        self.assertEqual(await manager.get_perm_level(evt, MEMBER), level.SUPERUSER)
        self.request.return_value = {"member_role": "member"}
        self.assertEqual(
            await manager.get_perm_level(evt, "OTHER_MEMBER"), level.MEMBER
        )
        self.assertEqual(await manager.get_perm_level(evt, BOT), level.MEMBER)
        self.assertIn(
            "我没", await manager.perm_block(evt, level.ADMIN, "set_group_ban", False)
        )

    async def test_unknown_member_role_fails_closed(self):
        manager, level = permissions()
        manager.cfg.admins_id = []
        self.request.side_effect = RuntimeError("11253")
        self.assertEqual(
            await manager.get_perm_level(
                qq.OfficialEvent(event(bot=self.bot), self.client), MEMBER
            ),
            level.UNKNOWN,
        )


class OfficialParsingTests(unittest.TestCase):
    def test_literal_and_structured_mentions_exclude_bot_and_deduplicate(self):
        evt = event(text=f"禁言 30 <@{BOT}> <@!{MEMBER}> <@{MEMBER}>")
        evt.get_messages = lambda: [
            {"type": "at", "data": {"qq": BOT}},
            {"type": "at", "data": {"qq": MEMBER}},
        ]
        self.assertEqual(qq.mention_ids(evt), [MEMBER])

    def test_raw_sdk_content_supplies_missing_target_mentions(self):
        evt = event()
        evt.message_obj.raw_message = types.SimpleNamespace(
            raw_data={"content": f"<@{MEMBER}>"}
        )
        self.assertEqual(qq.mention_ids(evt), [MEMBER])

    def test_onebot_does_not_parse_literal_mentions(self):
        self.assertEqual(
            qq.mention_ids(event(text=f"<@{MEMBER}>", platform="aiocqhttp")), []
        )

    def test_official_join_qa_and_request_flag_are_normalized(self):
        evt = event()
        evt.message_obj.raw_message = {
            "group_openid": GROUP,
            "member_openid": MEMBER,
            "join_request_id": "SYNTHETIC_REQUEST",
            "verify_info": {
                "review_qa_list": [{"question": "Synthetic?", "answer": "Answer"}]
            },
        }
        raw = qq.raw_event(evt)
        self.assertEqual(
            (raw["post_type"], raw["group_id"], raw["user_id"]),
            ("request", GROUP, MEMBER),
        )
        self.assertIn("答案：Answer", raw["comment"])
        self.assertTrue(raw["flag"].startswith("qqofficial:"))


if __name__ == "__main__":
    unittest.main()
