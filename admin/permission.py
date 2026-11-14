import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from enum import IntEnum
from functools import wraps
from typing import Any, cast

from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .config import PluginConfig
from .data import QQAdminDB
from .utils import get_ats
from ..qq_official import OfficialAPIError, adapt_event, is_official


class PermLevel(IntEnum):
    """
    定义用户的权限等级。数字越小，权限越高。
    """

    SUPERUSER = 0
    OWNER = 1
    ADMIN = 2
    HIGH = 3
    MEMBER = 4
    UNKNOWN = 5

    def __str__(self):
        return {
            PermLevel.SUPERUSER: "超管",
            PermLevel.OWNER: "群主",
            PermLevel.ADMIN: "管理员",
            PermLevel.HIGH: "高等级成员",
            PermLevel.MEMBER: "成员",
            PermLevel.UNKNOWN: "未知/无权限",
        }.get(self, "未知/无权限")

    @classmethod
    def from_str(cls, perm_str: str):
        mapping = {
            "超管": cls.SUPERUSER,
            "群主": cls.OWNER,
            "管理员": cls.ADMIN,
            "高等级成员": cls.HIGH,
            "成员": cls.MEMBER,
            "未知": cls.UNKNOWN,
            "无权限": cls.UNKNOWN,
        }
        return mapping.get(perm_str, cls.UNKNOWN)


class PermissionManager:
    _initialized = False

    def __init__(self):
        self.cfg: PluginConfig | None = None
        self.db: QQAdminDB | None = None

    def lazy_init(self, config: PluginConfig, db: QQAdminDB):
        if self._initialized:
            raise RuntimeError("PermissionManager already initialized")
        self.cfg = config
        self.db = db
        self._initialized = True

    def refresh(self, config: PluginConfig, db: QQAdminDB | None = None, host=None):
        self.cfg = config
        self.host = host
        if db is not None:
            self.db = db
        self._initialized = True

    async def get_perm_level(
        self, event: AiocqhttpMessageEvent, user_id: str | int
    ) -> PermLevel:
        group_id = event.get_group_id()
        if not group_id or not user_id or str(group_id) == "0" or str(user_id) == "0":
            return PermLevel.UNKNOWN
        if is_official(event):
            event = adapt_event(event)
            host = getattr(self, "host", None)
            if host is not None and str(user_id) == str(event.get_sender_id()):
                event._operator_blocked = host._is_group_admin_blocked(
                    group_id, str(user_id)
                )
                event._operator_admin = await host._is_admin(event)
            if str(user_id) == str(event.get_sender_id()) and getattr(
                event, "_operator_blocked", False
            ):
                return PermLevel.UNKNOWN
            if str(user_id) == str(event.get_sender_id()) and (
                getattr(event, "_operator_admin", False)
                or event.is_admin()
                or (self.cfg and str(user_id) in self.cfg.admins_id)
            ):
                return PermLevel.SUPERUSER
            try:
                if str(user_id) == str(event.get_self_id()):
                    info = await event.bot.bot_state(group_id)
                else:
                    info = await event.bot.get_group_member_info(
                        group_id=group_id, user_id=str(user_id)
                    )
                return {
                    "owner": PermLevel.OWNER,
                    "admin": PermLevel.ADMIN,
                    "member": PermLevel.MEMBER,
                }.get(info.get("member_role", info.get("role")), PermLevel.UNKNOWN)
            except OfficialAPIError:
                return PermLevel.UNKNOWN
        if self.cfg and str(user_id) in self.cfg.admins_id:
            return PermLevel.SUPERUSER
        try:
            info = await event.bot.get_group_member_info(
                group_id=int(group_id), user_id=int(user_id), no_cache=True
            )
        except Exception:
            return PermLevel.UNKNOWN
        role = info.get("role", "unknown")
        level = int(info.get("level", 0))
        group_config = (
            self.db.get_group_snapshot(group_id)
            if self.db is not None
            else {"level_threshold": self.cfg.level_threshold if self.cfg else 50}
        )
        level_threshold = int(group_config.get("level_threshold", 50))
        match role:
            case "owner":
                return PermLevel.OWNER
            case "admin":
                return PermLevel.ADMIN
            case "member":
                return PermLevel.HIGH if level >= level_threshold else PermLevel.MEMBER
            case _:
                return PermLevel.UNKNOWN

    async def perm_block(
        self,
        event: AiocqhttpMessageEvent,
        bot_perm: PermLevel,
        perm_key: str,
        check_at: bool = True,
    ) -> str | None:
        user_level = await self.get_perm_level(event, user_id=event.get_sender_id())

        # 未指定权限，则默认至少需要管理员权限
        group_config = (
            self.db.get_group_snapshot(event.get_group_id())
            if self.db is not None
            else {"perms": self.cfg.perms if self.cfg else {}}
        )
        perms = group_config.get("perms", {})
        required_level = PermLevel.from_str(str(perms.get(perm_key, "管理员")))

        if user_level > required_level:
            return f"你没{required_level}权限"

        if is_official(event):
            unsupported = {
                "whole_ban": "全体禁言",
                "set_group_card": "修改群名片",
                "set_group_special_title": "修改头衔",
                "set_group_special_title_me": "申请头衔",
                "admin": "任免管理员",
                "essence": "设置精华",
                "get_essence_msg_list": "群精华列表",
                "set_group_portrait": "修改群头像",
                "set_group_name": "修改群名",
                "send_group_notice": "群公告",
                "get_group_notice": "群公告",
                "curfew": "宵禁全禁",
                "upload_group_file": "管理群文件",
                "delete_group_file": "管理群文件",
                "view_group_file": "管理群文件",
            }
            if perm_key in unsupported:
                return f"QQ官方平台暂未提供本插件所需的{unsupported[perm_key]}接口，请通过OneBot使用该功能"

        bot_level = await self.get_perm_level(event, user_id=event.get_self_id())
        if bot_level > bot_perm:
            return f"我没{bot_perm}权限"

        if check_at:
            for at_id in get_ats(event):
                at_level = await self.get_perm_level(event, user_id=at_id)
                if bot_level >= at_level:
                    return f"我动不了{at_level}"

        return None

    async def llm_perm_block(
        self,
        event: AiocqhttpMessageEvent,
        perm_key: str,
        bot_perm: PermLevel = PermLevel.ADMIN,
    ) -> str | None:
        if event.platform_meta.name != "aiocqhttp" and not is_official(event):
            return "该工具仅支持通过 QQ 群聊调用"

        if event.is_private_chat():
            return "该工具仅支持在 QQ 群聊中调用"

        if not self._initialized:
            logger.error(
                "PermissionManager is not initialized while checking LLM tool permission: %s",
                perm_key,
            )
            return "内部错误：权限系统尚未正确加载"

        return await self.perm_block(
            event,
            bot_perm=bot_perm,
            perm_key=perm_key,
            check_at=False,
        )


perm_manager = PermissionManager()


def perm_required(
    bot_perm: PermLevel = PermLevel.ADMIN,
    perm_key: str | None = None,
    check_at: bool = True,
):
    """
    权限检查装饰器。
    :param perm_key: 可选。用户执行命令所需的最低权限键名，默认使用被装饰函数的函数名。
    :param bot_perm: Bot 执行此命令所需的最低权限等级。
    :param check_at: 是否检查“是否有权对被@者实施操作”。
    """

    def decorator(
        func: Callable[..., AsyncGenerator[Any, Any] | Awaitable[Any]],
    ) -> Callable[..., AsyncGenerator[Any, Any]]:
        actual_perm_key = perm_key or func.__name__

        @wraps(func)
        async def wrapper(
            plugin_instance: Any,
            event: AiocqhttpMessageEvent,
            *args: Any,
            **kwargs: Any,
        ) -> AsyncGenerator[Any, Any]:

            # 仅限aiocqhttp
            if event.platform_meta.name != "aiocqhttp" and not is_official(event):
                return

            event = (
                adapt_event(event, await plugin_instance._get_client(event))
                if is_official(event)
                else event
            )
            if is_official(event):
                event._operator_blocked = plugin_instance._is_group_admin_blocked(
                    event.get_group_id(), event.get_sender_id()
                )
                event._operator_admin = await plugin_instance._is_admin(event)

            # 仅限群聊
            if event.is_private_chat():
                return

            # 权限管理未初始化
            if not perm_manager._initialized:
                logger.error(
                    f"PermissionManager 未初始化（尝试访问权限项：{perm_key}）"
                )
                yield event.plain_result("内部错误：权限系统未正确加载")
                event.stop_event()
                return

            # 判断权限
            result = await perm_manager.perm_block(
                event, bot_perm=bot_perm, perm_key=actual_perm_key, check_at=check_at
            )
            if result:
                if is_official(event):
                    result += f"\n官方平台按OpenID授权；你的OpenID：{event.get_sender_id()}。成员资料接口未开通时，请将此ID加入插件管理员或本群超管。"
                yield event.plain_result(result)
                event.stop_event()
                return

            # 执行原始方法
            try:
                if inspect.isasyncgenfunction(func):
                    async for item in func(plugin_instance, event, *args, **kwargs):
                        yield item
                else:
                    await cast(
                        Awaitable[Any], func(plugin_instance, event, *args, **kwargs)
                    )
            except OfficialAPIError as exc:
                yield event.plain_result(str(exc))
                event.stop_event()

        return wrapper

    return decorator
