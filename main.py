"""拒绝 AI 骚扰插件。

功能：
1. ``end_session`` 工具：**多人**同时骚扰时，AI 结束整个会话。
   结束后该聊天任何消息都不发给 AI，只回复固定文案；``/new`` 才能恢复。
2. ``block_user`` 工具：**某一个用户**持续骚扰时，AI 精准拉黑该用户。
   被拉黑用户的消息不进 AI，只回复拉黑提示。
3. 把当前发送者的昵称 / 用户 ID / 平台 / 群号注入系统提示词，
   让 AI 分得清"是谁在骚扰"，避免拉黑错人。
4. 管理员也可以被拉黑，但**指令永远放行**，便于管理员自行解封。

权限说明：
- 解封/查看类指令使用 ``filter.PermissionType.ADMIN``。
  AstrBot 中 ``role == "admin"`` 只由配置项 ``admins_id`` 决定
  （见 waking_check/stage.py），因此是 **AstrBot 管理员**，
  **群聊管理员不会被识别**，符合预期。

对应 AstrBot 4.x 消息管道的挂载点：
- ``@filter.llm_tool``：注册 AI 可调用的函数工具。
- ``@filter.on_llm_request(priority=10000)``：真正发起 LLM 请求前拦截，
  这是"不发给 AI"最可靠的位置。
- ``@filter.command`` / ``command_group``：管理员解封入口。
"""

from __future__ import annotations

import re
import time
from typing import Any

from astrbot.api import AstrBotConfig, logger, sp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star

# 会话结束状态：umo 作用域，每个聊天独立
SESSION_SCOPE = "umo"
SESSION_KEY = "astrbot_plugin_reject_harassment_session"

# 拉黑名单：plugin 作用域，整个插件共享
BLOCK_SCOPE = "plugin"
BLOCK_KEY = "blocklist"

FALLBACK_PLUGIN_ID = "celraw/astrbot_plugin_reject_harassment"

DEFAULT_NOTICE = "AI可能无法帮助你，请开启新对话"
DEFAULT_BLOCKED_NOTICE = "你已被拉黑，无法继续对话"

# 识别指令事件时用的过滤器类名
COMMAND_FILTER_CLASS_NAMES = {"CommandFilter", "CommandGroupFilter"}


class RejectHarassmentPlugin(Star):
    """拒绝 AI 骚扰：拉黑单个用户，或结束整个会话。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}

    # ==================================================================
    # 配置
    # ==================================================================
    @property
    def notice_text(self) -> str:
        return str(self.config.get("notice_text") or "").strip() or DEFAULT_NOTICE

    @property
    def blocked_notice_text(self) -> str:
        return (
            str(self.config.get("blocked_notice_text") or "").strip()
            or DEFAULT_BLOCKED_NOTICE
        )

    @property
    def block_scope(self) -> str:
        scope = str(self.config.get("block_scope") or "global").strip().lower()
        return scope if scope in {"global", "session"} else "global"

    def _flag(self, key: str, default: bool = True) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)

    def _plugin_id(self) -> str:
        return getattr(self, "plugin_id", None) or FALLBACK_PLUGIN_ID

    # ==================================================================
    # 会话结束状态
    # ==================================================================
    async def _get_session_state(self, umo: str) -> dict[str, Any] | None:
        try:
            state = await sp.get_async(SESSION_SCOPE, umo, SESSION_KEY, default=None)
        except Exception as e:
            logger.error(f"读取会话结束状态失败: {e}", exc_info=True)
            return None
        if isinstance(state, dict) and state.get("ended"):
            return state
        return None

    async def _mark_session_ended(self, umo: str, reason: str = "") -> None:
        cid = await self._current_conversation_id(umo)
        try:
            await sp.put_async(
                SESSION_SCOPE,
                umo,
                SESSION_KEY,
                {
                    "ended": True,
                    "reason": str(reason or ""),
                    "conversation_id": cid or "",
                    "at": time.time(),
                },
            )
        except Exception as e:
            logger.error(f"写入会话结束状态失败: {e}", exc_info=True)

    async def _clear_session_state(self, umo: str) -> bool:
        was_ended = await self._get_session_state(umo) is not None
        try:
            await sp.remove_async(SESSION_SCOPE, umo, SESSION_KEY)
        except Exception as e:
            logger.error(f"清除会话结束状态失败: {e}", exc_info=True)
        return was_ended

    async def _current_conversation_id(self, umo: str) -> str | None:
        try:
            return await self.context.conversation_manager.get_curr_conversation_id(umo)
        except Exception as e:
            logger.debug(f"获取当前对话 ID 失败: {e}")
            return None

    # ==================================================================
    # 拉黑名单
    # ==================================================================
    async def _load_blocklist(self) -> dict[str, dict]:
        try:
            data = await sp.get_async(
                BLOCK_SCOPE,
                self._plugin_id(),
                BLOCK_KEY,
                default=None,
            )
        except Exception as e:
            logger.error(f"读取拉黑名单失败: {e}", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    async def _save_blocklist(self, data: dict[str, dict]) -> None:
        try:
            await sp.put_async(BLOCK_SCOPE, self._plugin_id(), BLOCK_KEY, data)
        except Exception as e:
            logger.error(f"写入拉黑名单失败: {e}", exc_info=True)

    @staticmethod
    def _global_key(event: AstrMessageEvent) -> str:
        platform = event.get_platform_id() or event.get_platform_name()
        return f"{platform}:{event.get_sender_id()}"

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        platform = event.get_platform_id() or event.get_platform_name()
        return f"{event.unified_msg_origin}|{platform}:{event.get_sender_id()}"

    def _target_keys(self, event: AstrMessageEvent) -> list[str]:
        """匹配时同时看全局键和会话键，避免切换范围后旧记录失效。"""
        return [self._global_key(event), self._session_key(event)]

    async def _match_blocked(self, event: AstrMessageEvent) -> dict | None:
        """当前发送者是否被拉黑。"""
        if not event.get_sender_id():
            return None
        blocklist = await self._load_blocklist()
        if not blocklist:
            return None
        for key in self._target_keys(event):
            if key in blocklist:
                return blocklist[key]
        return None

    async def _is_blocked_by_id(self, event: AstrMessageEvent, user_id: str) -> bool:
        """指定 user_id 是否在当前会被拦（全局或本会话）。"""
        blocklist = await self._load_blocklist()
        platform = event.get_platform_id() or event.get_platform_name()
        keys = [
            f"{platform}:{user_id}",
            f"{event.unified_msg_origin}|{platform}:{user_id}",
        ]
        return any(key in blocklist for key in keys)

    async def _add_blocked(
        self,
        event: AstrMessageEvent,
        user_id: str,
        reason: str,
    ) -> bool:
        """加入拉黑名单，成功返回 True。"""
        if not user_id:
            return False
        platform = event.get_platform_id() or event.get_platform_name()
        blocklist = await self._load_blocklist()

        entry = {
            "user_id": user_id,
            "name": event.get_sender_name() if user_id == event.get_sender_id() else "",
            "platform": platform,
            "umo": event.unified_msg_origin,
            "reason": str(reason or ""),
            "at": time.time(),
        }

        if self.block_scope == "session":
            key = f"{event.unified_msg_origin}|{platform}:{user_id}"
            blocklist[key] = entry
        else:
            key = f"{platform}:{user_id}"
            blocklist[key] = entry
            # 全局拉黑时，清理同一用户的本会话条目，避免残留
            prefix = f"{platform}:{user_id}"
            for old_key in [
                k for k in blocklist if k.endswith(f"|{prefix}") and k != key
            ]:
                blocklist.pop(old_key, None)

        await self._save_blocklist(blocklist)
        return True

    async def _remove_blocked_by_id(self, user_id: str) -> int:
        """解封某用户，返回移除条目数。"""
        blocklist = await self._load_blocklist()
        doomed = [
            key
            for key, entry in blocklist.items()
            if str(entry.get("user_id")) == str(user_id)
        ]
        for key in doomed:
            blocklist.pop(key, None)
        if doomed:
            await self._save_blocklist(blocklist)
        return len(doomed)

    # ==================================================================
    # 工具 1：拉黑某个用户（单人持续骚扰）
    # ==================================================================
    @filter.llm_tool(name="block_user")
    async def block_user(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        reason: str = "",
    ) -> str:
        """拉黑一个持续骚扰你的用户，之后不再理会ta的任何消息。

        当**某一个特定用户**持续辱骂、刷屏、恶意攻击你，
        而你只想屏蔽这一个人（其他用户仍可正常交流）时，调用本工具。
        请从系统提示中的「当前消息发送者信息」里读取用户ID：
        - 想拉黑正在发言的这个人：user_id 留空即可，或用上面的用户ID；
        - 不要凭猜测填写 ID，填错会误伤其他用户。

        Args:
            user_id(string): 要拉黑的用户ID。留空表示拉黑当前正在发言的用户。
            reason(string): 拉黑原因，一句话简要说明。
        """
        target_id = self._resolve_user_id(event, user_id, allow_at=False) or str(
            event.get_sender_id() or ""
        ).strip()
        if not target_id:
            return "无法获取用户ID，拉黑失败。"
        if target_id == str(event.get_self_id()):
            return "不能拉黑机器人自己。"

        if await self._is_blocked_by_id(event, target_id):
            return f"用户 {target_id} 已在拉黑名单中。"

        ok = await self._add_blocked(event, target_id, reason)
        if not ok:
            return "拉黑失败，请稍后重试。"

        logger.info(
            f"AI 拉黑用户: umo={event.unified_msg_origin}, "
            f"user_id={target_id}, reason={reason!r}, scope={self.block_scope}"
        )

        if self._flag("notify_on_block", True):
            await self._reply(event, self.blocked_notice_text)

        event.stop_event()
        return f"已拉黑用户 {target_id}。此后不再处理该用户的消息。"

    # ==================================================================
    # 工具 2：结束整个会话（多人骚扰）
    # ==================================================================
    @filter.llm_tool(name="end_session")
    async def end_session(self, event: AstrMessageEvent, reason: str = "") -> str:
        """结束当前整个会话，不再回复这个聊天里的任何消息。

        当**多个人**同时骚扰、或整个群的氛围已经无法正常交流时，
        调用本工具结束整个会话（而不是只拉黑某一个人）。
        若只是某一个用户持续骚扰，请改用 block_user 只拉黑那个人。
        会话结束后，该聊天的新消息都不会发送给你，只会回复一句提示。
        只有用户发送 /new 开启新对话才能恢复。

        Args:
            reason(string): 结束会话的原因，一句话简要说明。
        """
        umo = event.unified_msg_origin
        await self._mark_session_ended(umo, reason)
        logger.info(f"AI 结束会话: umo={umo}, reason={reason!r}")

        if self._flag("notify_on_end", True):
            await self._reply(event, self.notice_text)

        event.stop_event()
        return "会话已结束。此后本会话的消息不会再发送给你。"

    # ==================================================================
    # 拦截：不把消息发给 AI
    # ==================================================================
    @filter.on_llm_request(priority=10000)
    async def guard_llm_request(
        self,
        event: AstrMessageEvent,
        req: Any = None,
    ) -> None:
        """已结束 / 已拉黑时拦截 LLM 请求；指令一律放行。"""
        umo = event.unified_msg_origin

        # 1) 指令永远放行：管理员可以借此解封，机器人也不会被指令卡死
        if self._is_command_event(event):
            return

        # 2) 被拉黑用户
        block_entry = await self._match_blocked(event)
        if block_entry:
            logger.info(
                f"用户已被拉黑，拦截消息不发给 AI: umo={umo}, "
                f"user_id={event.get_sender_id()}"
            )
            await self._reply(event, self.blocked_notice_text)
            event.stop_event()
            return

        # 3) 会话已结束
        state = await self._get_session_state(umo)
        if state:
            if await self._is_new_conversation(umo, state):
                await self._clear_session_state(umo)
                logger.info(f"检测到新对话，会话 {umo} 已恢复。")
            else:
                logger.info(
                    f"会话已结束，拦截消息不发给 AI: umo={umo}, "
                    f"message={(event.get_message_str() or '')[:60]!r}"
                )
                await self._reply(event, self.notice_text)
                event.stop_event()
                return

        # 4) 把发送者身份注入给 AI，便于它分清是谁在骚扰
        if self._flag("inject_sender_info", True):
            self._inject_sender_info(event, req)

    # ==================================================================
    # 撤销：/new 恢复会话
    # ==================================================================
    @filter.command("new", priority=1000)
    async def revive_on_new(self, event: AstrMessageEvent):
        """用户发送 /new 时清除会话结束状态，随后由内置 /new 创建新对话。"""
        umo = event.unified_msg_origin
        if not await self._clear_session_state(umo):
            return

        logger.info(f"会话 {umo} 已通过 /new 恢复。")
        if self._is_builtin_commands_disabled():
            yield event.plain_result("已开启新对话，AI 恢复对话。")

    # ==================================================================
    # 管理员指令：解封 / 查看 / 手动拉黑
    # 注意：PermissionType.ADMIN 依据 AstrBot 的 admins_id，
    #       群聊管理员不会被授权。
    # ==================================================================
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("unblock")
    async def unblock_command(self, event: AstrMessageEvent, user_id: str = ""):
        """解封指定用户：/unblock <用户ID 或 @某人>（仅 AstrBot 管理员）"""
        target = self._resolve_user_id(event, user_id)
        if not target:
            yield event.plain_result(
                "没识别到用户ID。用法：/unblock <用户ID>，或在群里 @ 那个人。"
            )
            return
        removed = await self._remove_blocked_by_id(target)
        if removed:
            logger.info(f"管理员解封用户 {target}，移除 {removed} 条记录。")
            yield event.plain_result(f"已解封用户 {target}（移除 {removed} 条记录）。")
        else:
            yield event.plain_result(f"用户 {target} 不在拉黑名单中。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blocklist")
    async def blocklist_command(self, event: AstrMessageEvent):
        """查看当前拉黑名单（仅 AstrBot 管理员）"""
        blocklist = await self._load_blocklist()
        if not blocklist:
            yield event.plain_result("拉黑名单为空。")
            return
        lines = [f"拉黑名单（共 {len(blocklist)} 条）："]
        for key, entry in blocklist.items():
            name = entry.get("name") or "?"
            uid = entry.get("user_id") or "?"
            reason = entry.get("reason") or "-"
            scope = "会话" if "|" in key else "全局"
            lines.append(f"- [{scope}] {name}({uid}) 原因：{reason}")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("block")
    async def block_command(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        reason: str = "",
    ):
        """手动拉黑用户：/block <用户ID 或 @某人> [原因]（仅 AstrBot 管理员）"""
        target = self._resolve_user_id(event, user_id)
        if not target:
            yield event.plain_result(
                "没识别到用户ID。用法：/block <用户ID> [原因]，或在群里 @ 那个人。"
            )
            return
        if target == str(event.get_self_id()):
            yield event.plain_result("不能拉黑机器人自己。")
            return
        if await self._add_blocked(event, target, reason):
            yield event.plain_result(f"已拉黑用户 {target}（范围：{self.block_scope}）。")
        else:
            yield event.plain_result("拉黑失败，请稍后重试。")

    # ==================================================================
    # 辅助
    # ==================================================================
    @staticmethod
    def _extract_at_ids(event: AstrMessageEvent) -> list[str]:
        """从消息链里取出所有被 @ 的用户ID（跳过 @全体成员）。"""
        ids: list[str] = []
        for comp in event.get_messages() or []:
            if not isinstance(comp, At):
                continue
            qq = str(getattr(comp, "qq", "") or "").strip()
            if qq and qq.lower() != "all":
                ids.append(qq)
        return ids

    def _resolve_user_id(
        self,
        event: AstrMessageEvent,
        raw: str = "",
        *,
        allow_at: bool = True,
    ) -> str:
        """把指令参数统一解析成纯用户ID。

        兼容三种写法：
        - 纯数字：``1096879696``
        - @ 某人：从消息链的 At 段取 ``qq``
        - 被平台渲染过的文本：``@昵称(1096879696)``，用正则兜底抽数字

        Args:
            event: 当前消息事件。
            raw: 指令参数原文。
            allow_at: 是否允许回退到消息里的 @ 目标。工具调用必须传 False，
                因为群里发消息通常 @ 的是机器人自己，会误解析成 bot 的 ID。
        """
        text = str(raw or "").strip().lstrip("@").strip()
        if text.isdigit():
            return text

        if allow_at:
            at_ids = self._extract_at_ids(event)
            if at_ids:
                return at_ids[0]

        match = re.search(r"\d{5,}", str(raw or ""))
        if match:
            return match.group()

        return ""

    @staticmethod
    def _is_command_event(event: AstrMessageEvent) -> bool:
        """本条消息是否触发了指令（含指令组）。"""
        handlers = event.get_extra("activated_handlers", []) or []
        for handler in handlers:
            for event_filter in getattr(handler, "event_filters", []) or []:
                if type(event_filter).__name__ in COMMAND_FILTER_CLASS_NAMES:
                    return True
        return False

    async def _is_new_conversation(self, umo: str, state: dict[str, Any]) -> bool:
        """当前对话 ID 与结束时的不同，说明用户开了新对话。"""
        old_cid = str(state.get("conversation_id") or "").strip()
        if not old_cid:
            return False
        current_cid = await self._current_conversation_id(umo)
        if not current_cid:
            return False
        return current_cid != old_cid

    def _inject_sender_info(self, event: AstrMessageEvent, req: Any) -> None:
        """把发送者身份写给 AI，避免它分不清是谁在骚扰而误拉黑。"""
        if req is None or not hasattr(req, "system_prompt"):
            return
        group_id = event.get_group_id()
        lines = [
            "",
            "【当前消息发送者信息】",
            f"- 昵称：{event.get_sender_name() or '未知'}",
            f"- 用户ID：{event.get_sender_id() or '未知'}",
            f"- 平台：{event.get_platform_name() or '未知'}",
        ]
        if group_id:
            lines.append(f"- 群号：{group_id}")
        lines.extend(
            [
                "- 若需要拉黑正在骚扰你的用户，请使用上面的「用户ID」调用 "
                "block_user；只拉黑单个骚扰者时不要结束整个会话。",
            ]
        )
        block = "\n".join(lines)
        try:
            base = req.system_prompt or ""
            req.system_prompt = f"{base}\n{block}" if base else block.lstrip("\n")
        except Exception as e:
            logger.debug(f"注入发送者信息失败: {e}")

    async def _reply(self, event: AstrMessageEvent, text: str) -> None:
        """发送固定文案。"""
        try:
            await event.send(event.plain_result(text))
            logger.info(f"已回复文案: {text}")
        except Exception as e:
            logger.error(f"发送提示失败: {e}", exc_info=True)

    def _is_builtin_commands_disabled(self) -> bool:
        try:
            return bool(self.context.get_config().get("disable_builtin_commands", False))
        except Exception:
            return False

    async def terminate(self) -> None:
        logger.info("拒绝AI骚扰插件已卸载。")
