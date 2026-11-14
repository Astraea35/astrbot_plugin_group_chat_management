"""QQ group management through the authenticated AstrBot botpy client."""

import asyncio
import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone


OFFICIAL_PLATFORMS = {"qq_official", "qq_official_webhook"}


def is_official(event):
    try:
        return event.get_platform_name().strip().lower() in OFFICIAL_PLATFORMS
    except (AttributeError, TypeError):
        return (
            getattr(getattr(event, "platform_meta", None), "name", "")
            in OFFICIAL_PLATFORMS
        )


def action_id(event, value):
    """Preserve opaque IDs on official events and numeric IDs on OneBot."""
    return str(value) if is_official(event) else int(value)


def mention_ids(event):
    """Read both structured mentions and QQ's literal OpenID mention syntax."""
    targets = []
    texts = [getattr(event, "message_str", "") or ""]
    for seg in event.get_messages() or []:
        kind = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", "")
        data = seg.get("data", {}) if isinstance(seg, dict) else {}
        if kind == "at" or type(seg).__name__ == "At":
            targets.append(str(data.get("qq", "") if isinstance(seg, dict) else seg.qq))
        texts.append(
            str(
                data.get("text", "")
                if isinstance(seg, dict)
                else getattr(seg, "text", "")
            )
        )
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if is_official(event):
        payload = raw if isinstance(raw, dict) else getattr(raw, "raw_data", {})
        texts.append(
            str(
                payload.get("content", "")
                if isinstance(payload, dict)
                else getattr(raw, "content", "")
            )
        )
        for text in texts:
            targets.extend(re.findall(r"<@!?([A-Za-z0-9_-]+)>", text))
    own = str(event.get_self_id())
    return list(
        dict.fromkeys(t for t in targets if t and t not in {own, "all", "0", "None"})
    )


def request_flag(group_id, member_id, request_id):
    data = json.dumps([group_id, member_id, request_id], separators=(",", ":")).encode()
    return "qqofficial:" + base64.urlsafe_b64encode(data).decode()


def raw_event(event):
    """Normalize official messages and member events without changing their IDs."""
    raw = getattr(event, "raw_event", None)
    if not isinstance(raw, dict):
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if isinstance(raw, dict) and raw.get("post_type"):
        return raw
    if not is_official(event):
        return raw if isinstance(raw, dict) else None
    data = raw if isinstance(raw, dict) else getattr(raw, "raw_data", {})
    if not isinstance(data, dict):
        data = {}
    data = data.get("d", data)
    gid = str(data.get("group_openid") or event.get_group_id() or "")
    author = data.get("author") or {}
    uid = str(
        data.get("member_openid")
        or author.get("member_openid")
        or event.get_sender_id()
        or ""
    )
    result = {
        "group_id": gid,
        "user_id": uid,
        "self_id": str(event.get_self_id()),
        **data,
    }
    if data.get("join_request_id"):
        verify = data.get("verify_info") or {}
        comment = verify.get("verify_message", "")
        if verify.get("review_qa_list"):
            questions = "\n".join(
                str(qa.get("question", "")) for qa in verify["review_qa_list"]
            )
            answers = "\n".join(
                str(qa.get("answer", "")) for qa in verify["review_qa_list"]
            )
            comment = f"问题：{questions}\n答案：{answers}"
        result.update(
            post_type="request",
            request_type="group",
            sub_type="add",
            comment=comment,
            flag=request_flag(gid, uid, str(data["join_request_id"])),
        )
    else:
        result.update(
            post_type="message",
            message_type="group",
            message_id=str(getattr(event.message_obj, "message_id", "")),
        )
    return result


class OfficialAPIError(RuntimeError):
    pass


class OfficialClient:
    """Translate the plugin's existing management actions to documented QQ APIs."""

    def __init__(self, bot, group_id="", self_id="", event=None):
        self.bot = bot
        self.group_id = str(group_id)
        self.self_id = str(self_id)
        self.event = event
        self.api = self
        self._states = {}
        self._denied_until = {}

    async def request(self, method, path, **kwargs):
        from botpy.http import Route

        denied_key = (method, re.sub(r"/members/[^/]+$", "/members/*", path))
        blocked = self._denied_until.get(denied_key, 0)
        if blocked > time.monotonic():
            raise OfficialAPIError(
                "QQ官方接口权限未开通（11253）；请在开放平台申请权限，或使用OpenID配置插件管理员"
            )
        try:
            http = self.bot.api._http
            route = Route(method, path)
            if callable(getattr(http, "check_session", None)):
                from aiohttp import ClientTimeout

                # botpy discards HTTP status and structured error codes, and returns
                # None for both a successful 204 and a timeout. Keep that evidence.
                await asyncio.wait_for(http.check_session(), timeout=20)
                route.is_sandbox = http.is_sandbox
                async with http._session.request(
                    method=method,
                    url=route.url,
                    headers=http._headers,
                    timeout=ClientTimeout(total=20),
                    **kwargs,
                ) as response:
                    body = await response.read()
                    result = json.loads(body) if body else {}
                    if not 200 <= response.status < 300:
                        code = result.get(
                            "code", result.get("err_code", response.status)
                        )
                        if (
                            str(code) == "11253"
                            or str(result.get("err_code")) == "40012010"
                        ):
                            self._denied_until[denied_key] = time.monotonic() + 300
                        raise OfficialAPIError(f"QQ官方接口权限或请求失败（{code}）")
            else:
                result = await asyncio.wait_for(
                    http.request(route, **kwargs), timeout=20
                )
        except OfficialAPIError:
            raise
        except Exception as exc:
            text = str(exc)
            if "11253" in text or "40012010" in text or "无接口访问权限" in text:
                self._denied_until[denied_key] = time.monotonic() + 300
                raise OfficialAPIError(
                    "QQ官方接口权限未开通（11253）；请在开放平台申请权限，或使用OpenID配置插件管理员"
                ) from None
            raise OfficialAPIError(
                "QQ官方API调用失败，请检查机器人群管理权限及接口权限"
            ) from None
        if result is None:
            raise OfficialAPIError("QQ官方API未返回结果，操作状态未知")
        if not isinstance(result, dict):
            raise OfficialAPIError("QQ官方API返回格式错误")
        if result.get("code") or result.get("err_code"):
            code = result.get("code", result.get("err_code"))
            if str(code) == "11253" or str(result.get("err_code")) == "40012010":
                self._denied_until[denied_key] = time.monotonic() + 300
            raise OfficialAPIError(
                f"QQ官方接口调用失败（{code}），请检查接口权限及群管理权限"
            )
        return result

    async def bot_state(self, group_id):
        gid = str(group_id)
        cached = self._states.get(gid)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        state = await self.request("GET", f"/v2/groups/{gid}/bot_state")
        self._states[gid] = (time.monotonic(), state)
        return state

    async def call_action(self, action, **params):
        gid = str(params.get("group_id") or self.group_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", gid):
            raise OfficialAPIError("缺少有效的官方群OpenID")
        base = f"/v2/groups/{gid}"
        uid = str(params.get("user_id", ""))
        if (
            action in {"set_group_ban", "set_group_kick", "get_group_member_info"}
            and uid != self.self_id
            and (not re.fullmatch(r"[A-Za-z0-9_-]+", uid) or uid.isdigit())
        ):
            raise OfficialAPIError(
                "官方平台请@目标或填写该应用的成员OpenID，不能直接填写QQ号"
            )
        if action == "get_group_member_info":
            if uid == self.self_id:
                data = await self.bot_state(gid)
            else:
                data = await self.request("GET", base + f"/members/{uid}")
            return {
                **data,
                "user_id": data.get("member_openid", uid),
                "nickname": data.get("username", ""),
                "role": data.get("member_role", "unknown"),
                "is_robot": bool(data.get("bot", False)),
            }
        if action == "get_group_info":
            return await self.request("GET", base + "/info")
        if action == "get_group_member_list":
            members, cursor, seen = [], "", set()
            while True:
                data = await self.request(
                    "GET", base + "/members", params={"cursor": cursor}
                )
                members.extend(
                    {
                        **m,
                        "user_id": m["member_openid"],
                        "nickname": m.get("username", ""),
                        "role": m.get("member_role", "unknown"),
                    }
                    for m in data.get("members", [])
                )
                cursor = data.get("next_cursor", "")
                if not cursor:
                    return members
                if cursor in seen or len(seen) >= 100:
                    raise OfficialAPIError("成员列表分页未完成，请稍后重试")
                seen.add(cursor)
        if action == "set_group_ban":
            duration = int(params.get("duration", 0))
            if not 0 <= duration <= 30 * 86400:
                raise OfficialAPIError("官方禁言时长须在0至30天之间")
            expires = (
                (datetime.now(timezone.utc) + timedelta(seconds=duration))
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
                if duration
                else ""
            )
            return await self.request(
                "POST",
                base + "/restrict_chat_setting",
                json={
                    "members": [
                        {
                            "op": "add" if duration else "del",
                            "member_openid": uid,
                            "mute_expire_at": expires,
                        }
                    ]
                },
            )
        if action == "get_group_shut_list":
            data = await self.request("GET", base + "/restrict_chat_setting")
            return [
                {
                    **m,
                    "user_id": m["member_openid"],
                    "shut_up_timestamp": int(
                        datetime.fromisoformat(
                            m["mute_expire_at"].replace("Z", "+00:00")
                        ).timestamp()
                    ),
                }
                for m in data.get("members", [])
            ]
        if action == "set_group_kick":
            data = await self.request(
                "POST",
                base + "/batch_remove_members",
                json={
                    "member_openids": [uid],
                    "add_to_member_blacklist": bool(
                        params.get("reject_add_request", False)
                    ),
                },
            )
            if data.get("remove_members_result") != "success":
                raise OfficialAPIError("官方踢人接口未确认成功")
            if uid in data.get("add_to_member_blacklist_fail_openids", []):
                raise OfficialAPIError("成员已移出，但加入官方群黑名单失败")
            return data
        if action == "delete_msg":
            mid = str(params.get("message_id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.=-]+", mid):
                raise OfficialAPIError("缺少有效的官方消息ID；OneBot消息序号不能替代")
            return await self.request("DELETE", base + f"/messages/{mid}")
        if action == "set_group_add_request":
            try:
                flag = str(params["flag"])
                if not flag.startswith("qqofficial:"):
                    raise ValueError
                group, member, request_id = json.loads(
                    base64.b64decode(
                        flag.split(":", 1)[1], altchars=b"-_", validate=True
                    )
                )
                if (
                    str(group) != gid
                    or not re.fullmatch(r"[A-Za-z0-9_-]+", str(member))
                    or not request_id
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise OfficialAPIError("官方入群申请标识无效或来自其他群") from None
            approve = bool(params.get("approve"))
            body = {
                "op": "approve" if approve else "decline",
                "join_request_id": request_id,
            }
            if not approve:
                body["reject_reason"] = str(params.get("reason", ""))
            return await self.request(
                "POST", base + f"/approval_join_request/{member}", json=body
            )
        if action == "send_group_msg":
            content = params.get("message", "")
            if isinstance(content, list):
                content = "".join(
                    f"<@{part.get('data', {}).get('qq')}>"
                    if part.get("type") == "at"
                    else str(part.get("data", {}).get("text", ""))
                    for part in content
                )
            body = {"content": str(content), "msg_type": 0}
            if (
                self.event is not None
                and str(self.event.get_group_id()) == gid
                and self.event.message_obj.message_id
                and not params.get("proactive", False)
            ):
                body["msg_id"] = str(self.event.message_obj.message_id)
            data = await self.bot.api.post_group_message(group_openid=gid, **body)
            if not isinstance(data, dict) or not data.get("id"):
                raise OfficialAPIError("官方消息发送未确认成功")
            return {**data, "message_id": data["id"]}
        raise OfficialAPIError(
            f"QQ官方平台未提供本插件所需的 {action} 接口；该功能请通过OneBot使用"
        )

    def __getattr__(self, action):
        if action.startswith("_"):
            raise AttributeError(action)

        async def call(**params):
            return await self.call_action(action, **params)

        return call


class OfficialEvent:
    """Keep the original AstrBot event intact while adapting legacy handlers."""

    def __init__(self, event, client=None):
        self.original = event
        self.bot = client or OfficialClient(
            event.bot, event.get_group_id(), event.get_self_id(), event
        )

    def __getattr__(self, name):
        return getattr(self.original, name)


def adapt_event(event, client=None):
    if isinstance(event, OfficialEvent) or not is_official(event):
        return event
    return OfficialEvent(event, client)


async def poll_requests(plugin):
    """Cover missing member-event dispatch in older AstrBot/botpy releases."""
    from astrbot.api import logger
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.platform import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType

    groups = set(plugin._group_white_set)
    groups.update(str(g) for g in plugin._storage.list_configured_groups())
    groups.update(group for _, group in getattr(plugin, "_official_clients", {}))
    for gid in groups:
        if gid.isdigit() or not plugin.config.get("disclaimer_agreed", False):
            continue
        if not plugin._cfg("enabled", True, group_id=gid):
            continue
        ai_review = plugin._cfg(
            "join_audit_enabled", False, group_id=gid
        ) and plugin._resolve_join_rule(gid).get("enabled", True)
        legacy_review = await plugin._admin_db.get(gid, "join_switch", False)
        if not ai_review and not legacy_review:
            continue
        client = await plugin._client_for_group(gid)
        if not isinstance(client, OfficialClient):
            continue
        # Respect group scope before issuing even a read request.
        if gid in plugin._group_black_set or (
            plugin._group_white_set and gid not in plugin._group_white_set
        ):
            continue
        try:
            cursor, seen = "", set()
            while True:
                data = await client.request(
                    "GET",
                    f"/v2/groups/{gid}/join_request_list",
                    params={"limit": 50, "cursor": cursor},
                )
                for entry in data.get("list", []):
                    if (
                        entry.get("auto_approved")
                        or not entry.get("join_request_id")
                        or not entry.get("member_openid")
                    ):
                        continue
                    payload = {**entry, "group_openid": gid}
                    member = str(entry["member_openid"])
                    flag = request_flag(gid, member, str(entry["join_request_id"]))
                    # Pending manual reviews must not notify administrators on every poll.
                    notified = getattr(plugin, "_official_manual_requests", None)
                    if notified is None:
                        notified = plugin._official_manual_requests = {}
                    if len(notified) > 1000:
                        now = time.monotonic()
                        notified = plugin._official_manual_requests = {
                            k: v for k, v in notified.items() if now - v < 86400
                        }
                    if time.monotonic() - notified.get(flag, -86400) < 86400:
                        continue
                    message = AstrBotMessage()
                    message.type = MessageType.GROUP_MESSAGE
                    message.group_id = gid
                    message.self_id = client.self_id or "qq_official"
                    message.sender = MessageMember(
                        member, str(entry.get("username", ""))
                    )
                    message.message_id = ""
                    message.message = []
                    message.raw_message = payload
                    platform = client.bot.platform
                    event = AstrMessageEvent("", message, platform.meta(), gid)
                    event.bot = client.bot
                    event.raw_event = raw_event(event)

                    async def send_notice(result, target_client=client):
                        text = "".join(
                            str(getattr(part, "text", "")) for part in result.chain
                        )
                        await target_client.call_action(
                            "send_group_msg", message=text, proactive=True
                        )

                    event.send = send_notice
                    if ai_review:
                        if await plugin._handle_group_request(event):
                            notified[flag] = time.monotonic()
                    else:
                        handled = await plugin._admin_join.event_monitoring(
                            adapt_event(event, client)
                        )
                        if handled:
                            notified[flag] = time.monotonic()
                cursor = data.get("next_cursor", "")
                if not cursor:
                    break
                if cursor in seen or len(seen) >= 100:
                    raise OfficialAPIError("入群申请分页未完成")
                seen.add(cursor)
        except OfficialAPIError as exc:
            logger.debug("[GroupMgr] Official join request polling failed: %s", exc)
