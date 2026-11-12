"""Verify the user's revised admission and owner notification rules."""

import unittest

from test_membership_api_result import _Harness, _Event, onebot, utilities
from test_timeout_boundaries import moderation
from test_group_review_policy import _NoticeEvent


class _ProfileClient:
    def __init__(self, level=30, hidden=False, fail=False):
        self.level = level
        self.hidden = hidden
        self.fail = fail
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action == "get_stranger_info":
            if self.fail:
                raise ConnectionError("synthetic profile failure")
            return {"qqLevel": self.level, "isHideQQLevel": self.hidden, "status": 10}
        return {"status": "ok", "retcode": 0, "data": {}}


def _harness():
    h = _Harness()
    h.config["join_accept_keywords"] = []
    h.config["join_reject_keywords"] = ["交流学习", "学习交流"]
    h.cfg_values.update(
        {
            "join_empty_auto_accept": True,
            "join_min_qq_level": 30,
            "join_llm_moderation_enabled": True,
        }
    )
    return h


class AdmissionRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_application_at_level_thirty_is_approved_without_model(self):
        h = _harness()
        c = _ProfileClient(30)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = ""
        self.assertTrue(await h._handle_group_request(e))
        self.assertEqual(h.llm_calls, [])
        self.assertEqual(
            [x[0] for x in c.calls], ["get_stranger_info", "set_group_add_request"]
        )
        self.assertIs(c.calls[-1][1]["approve"], True)

    async def test_empty_application_below_threshold_is_rejected(self):
        h = _harness()
        c = _ProfileClient(29)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = "  "
        self.assertTrue(await h._handle_group_request(e))
        self.assertIs(c.calls[-1][1]["approve"], False)
        self.assertIn("29", c.calls[-1][1]["reason"])

    async def test_model_approval_cannot_bypass_level_requirement(self):
        h = _harness()
        c = _ProfileClient(28)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = "想玩MC"
        h.llm_response = '{"decision":"accept","reason":"正常游戏申请"}'
        self.assertTrue(await h._handle_group_request(e))
        self.assertIs(c.calls[-1][1]["approve"], False)

    async def test_hidden_level_is_transferred_to_manual(self):
        h = _harness()
        c = _ProfileClient(40, hidden=True)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = ""
        self.assertTrue(await h._handle_group_request(e))
        self.assertNotIn("set_group_add_request", [x[0] for x in c.calls])
        self.assertEqual(len(e.sent), 1)

    async def test_unknown_level_is_transferred_to_manual(self):
        h = _harness()
        c = _ProfileClient(None)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = ""
        self.assertTrue(await h._handle_group_request(e))
        self.assertNotIn("set_group_add_request", [x[0] for x in c.calls])
        self.assertEqual(len(e.sent), 1)

    async def test_profile_query_failure_is_transferred_to_manual(self):
        h = _harness()
        c = _ProfileClient(fail=True)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = ""
        self.assertTrue(await h._handle_group_request(e))
        self.assertNotIn("set_group_add_request", [x[0] for x in c.calls])

    async def test_owner_reject_phrases_skip_model_and_approval(self):
        for answer in ["进来交流学习", "进群学习交流", "大家好，交流学习一下"]:
            h = _harness()
            c = _ProfileClient(133)
            e = _NoticeEvent(c)
            e.raw_event["comment"] = answer
            self.assertTrue(await h._handle_group_request(e))
            self.assertEqual(h.llm_calls, [])
            self.assertEqual([x[0] for x in c.calls], ["set_group_add_request"])
            self.assertIs(c.calls[0][1]["approve"], False)

    async def test_specific_learning_answer_still_can_pass(self):
        h = _harness()
        c = _ProfileClient(40)
        e = _NoticeEvent(c)
        e.raw_event["comment"] = "想学习机械动力模组怎么玩"
        h.llm_response = '{"decision":"accept","reason":"正常学习游戏模组"}'
        self.assertTrue(await h._handle_group_request(e))
        self.assertIs(c.calls[-1][1]["approve"], True)


class _AlertClient:
    def __init__(self, private_ok=True):
        self.calls = []
        self.private_ok = private_ok

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action == "send_private_msg" and not self.private_ok:
            return {"status": "failed", "retcode": -1}
        return {"status": "ok", "retcode": 0, "data": {"message_id": 123}}


class _AlertHarness(
    moderation.ModerationMixin, onebot.OneBotMixin, utilities.UtilitiesMixin
):
    def __init__(self, client):
        self.client = client

    def _cfg(self, key, default=True, group_id=None):
        return {
            "moderation_notify_private_enabled": True,
            "moderation_notify_at_fallback": True,
        }.get(key, default)

    def _cfg_str(self, key, default="", group_id=None):
        return "10001" if key == "moderation_notify_admin_id" else default

    def _cfg_int(self, key, default=0, group_id=None):
        return 1800

    async def _get_client(self, event=None):
        return self.client


class ModerationNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_notice_is_only_sent_to_existing_owner(self):
        c = _AlertClient()
        h = _AlertHarness(c)
        ok = await h._notify_moderation_admin(
            None,
            "10000",
            "456",
            "synthetic",
            "[CQ:at,qq=all] synthetic message",
            "confirmed ad",
            True,
            True,
            [],
        )
        self.assertTrue(ok)
        self.assertEqual([x[0] for x in c.calls], ["send_private_msg"])
        self.assertEqual(c.calls[0][1]["user_id"], 10001)
        self.assertTrue(c.calls[0][1]["auto_escape"])
        self.assertIn("是否踢人由你决定", c.calls[0][1]["message"])
        self.assertIn("30分钟", c.calls[0][1]["message"])

    async def test_private_failure_mentions_owner_without_repeating_ad(self):
        c = _AlertClient(False)
        h = _AlertHarness(c)
        self.assertTrue(
            await h._notify_moderation_admin(
                None,
                "10000",
                "456",
                "synthetic",
                "private evidence",
                "confirmed ad",
                True,
                True,
                [],
            )
        )
        self.assertEqual(
            [x[0] for x in c.calls], ["send_private_msg", "send_group_msg"]
        )
        fallback = c.calls[1][1]["message"]
        self.assertEqual(fallback[0], {"type": "at", "data": {"qq": "10001"}})
        self.assertNotIn("private evidence", str(fallback))

    async def test_notification_reports_failed_actions_truthfully(self):
        c = _AlertClient()
        h = _AlertHarness(c)
        self.assertTrue(
            await h._notify_moderation_admin(
                None,
                "10000",
                "456",
                "synthetic",
                "",
                "confirmed ad",
                False,
                False,
                [],
            )
        )
        self.assertIn("未能撤回", c.calls[0][1]["message"])
        self.assertIn("未能禁言", c.calls[0][1]["message"])
        self.assertNotIn("set_group_kick", [x[0] for x in c.calls])
