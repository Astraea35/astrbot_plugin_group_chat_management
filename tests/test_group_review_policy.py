"""Keep ambiguous applications and provider failures out of punishment APIs."""

import unittest
import types
import io

from test_membership_api_result import _Harness, _Event, _Client, onebot, utilities
from test_timeout_boundaries import moderation, image_audit


class _NoticeEvent(_Event):
    def __init__(self, client):
        super().__init__(client)
        self.sent = []
        self.raw_event["flag"] = "slreq:1:42:999:1:12345"

    def plain_result(self, text):
        return text

    async def send(self, text):
        self.sent.append(text)


class ReviewPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_model_result_keeps_quote_flag_and_hides_advertisement(self):
        harness = _Harness()
        harness.config["join_accept_keywords"] = []
        harness.cfg_values["join_llm_moderation_enabled"] = True
        harness.llm_response = '{"decision":"manual","reason":"无法理解的长文字"}'
        client = _Client({"status": "ok", "retcode": 0})
        event = _NoticeEvent(client)
        event.raw_event["comment"] = (
            "synthetic private applicant text https://example.invalid/ad"
        )
        self.assertTrue(await harness._handle_group_request(event))
        self.assertEqual(client.calls, [])
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.sent[0].splitlines()[3], "flag：slreq:1:42:999:1:12345")
        self.assertNotIn("example.invalid", event.sent[0])
        self.assertIn("example.invalid", harness.logs[0][3])
        self.assertEqual(harness.logs[0][4], "入群转人工")
        self.assertEqual(harness.logs[0][5], "无法理解的长文字")
        self.assertTrue(await harness._handle_group_request(event))
        self.assertEqual(len(event.sent), 1)

    async def test_provider_failure_transfers_to_manual_even_with_local_hits(self):
        harness = _Harness()
        harness.config["join_accept_keywords"] = []
        harness.cfg_values.update(
            {"join_llm_moderation_enabled": True, "join_reject_use_lexicon": True}
        )
        harness.lexicon_hits = {"ad": True}
        harness.llm_response = ConnectionError("synthetic provider failure")
        client = _Client({"status": "ok", "retcode": 0})
        event = _NoticeEvent(client)
        self.assertTrue(await harness._handle_group_request(event))
        self.assertEqual(client.calls, [])
        self.assertEqual(len(event.sent), 1)

    async def test_empty_answer_transfers_to_manual_without_model_call(self):
        harness = _Harness()
        harness.config["join_accept_keywords"] = []
        harness.cfg_values["join_llm_moderation_enabled"] = True
        client = _Client({"status": "ok", "retcode": 0})
        event = _NoticeEvent(client)
        event.raw_event["comment"] = ""
        self.assertTrue(await harness._handle_group_request(event))
        self.assertEqual(harness.llm_calls, [])
        self.assertEqual(client.calls, [])
        self.assertEqual(len(event.sent), 1)

    async def test_fixed_provider_failure_does_not_try_other_models(self):
        calls = []

        class Harness(moderation.ModerationMixin):
            config = {"moderation_llm_provider_id": "selected"}

            async def _call_llm_by_provider_id(self, pid, *args):
                calls.append(pid)
                raise ConnectionError("synthetic failure")

        with self.assertRaises(RuntimeError):
            await Harness()._call_llm_safe("system", "prompt")
        self.assertEqual(calls, ["selected"])

    def test_model_confirmation_required_for_all_rule_fallbacks(self):
        class Harness(moderation.ModerationMixin):
            def _cfg(self, key, default=True, group_id=None):
                return key == "moderation_require_ai_confirmation"

        for hits in [
            {"oversized": True},
            {"obfuscated_url": True},
            {"swear": True},
            {"porn": True, "ad": True},
        ]:
            self.assertFalse(
                Harness()._llm_failure_requires_rule_penalty(
                    {"fallback": True}, hits, "synthetic message", "10000"
                )
            )

    def test_actual_qr_decoder_reads_synthetic_fixture(self):
        import qrcode

        target = "https://example.invalid/synthetic-minecraft-room"
        buffer = io.BytesIO()
        qrcode.make(target).save(buffer, format="PNG")
        decoder = image_audit._probe_qr_decoder()
        self.assertIsNotNone(decoder)
        self.assertIn(
            target, image_audit._decode_qr_from_bytes(buffer.getvalue(), decoder)
        )

    async def test_confirmed_ad_uses_delete_and_thirty_minute_mute(self):
        client = _Client({"status": "ok", "retcode": 0, "data": {}})

        class Harness(
            moderation.ModerationMixin, onebot.OneBotMixin, utilities.UtilitiesMixin
        ):
            _user_white_set = set()
            _group_white_set = {"10000"}
            _group_black_set = set()

            def __init__(self):
                self.logs = []

            async def _get_client(self, event=None):
                return client

            def _cfg(self, key, default=True, group_id=None):
                return {"llm_moderation_ban": True, "auto_moderate_notice": False}.get(
                    key, default
                )

            def _cfg_int(self, key, default=0, group_id=None):
                return 1800

            def _moderation_in_penalty_cooldown(self, *args):
                return False

            def _anti_flood_in_cooldown(self, *args):
                return False

            def _mark_moderation_penalty(self, *args):
                pass

            def _schedule_unban(self, *args):
                pass

            def _log_moderation(self, *args):
                self.logs.append(args)

        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(message_id="987"),
            get_group_id=lambda: "10000",
            get_sender_id=lambda: "456",
            stop_event=lambda: None,
        )
        harness = Harness()
        async for _ in harness._execute_llm_penalty(
            event,
            "10000",
            "456",
            "synthetic-user",
            "synthetic ad",
            "confirmed advertisement",
            "ad",
            [],
        ):
            pass
        self.assertEqual([x[0] for x in client.calls], ["delete_msg", "set_group_ban"])
        self.assertEqual(
            client.calls[1][1],
            {"group_id": 10000, "user_id": 456, "duration": 1800},
        )

    def test_other_group_is_outside_scope(self):
        class Harness(onebot.OneBotMixin):
            _group_white_set = {"10000"}
            _group_black_set = set()

            def _get_group_id(self, event):
                return "999"

        self.assertFalse(Harness()._check_group_access(object())[0])
