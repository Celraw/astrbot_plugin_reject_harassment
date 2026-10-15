"""离线功能测试：不启动 AstrBot，用假 event 验证核心状态机。

覆盖：
  T1  未结束时放行，并注入发送者身份给 AI
  T2  AI 调用 end_session → 标记结束 + 回复文案 + 终止本轮
  T3  结束后普通消息 → 只回文案，不发给 AI
  T4  结束后 /new → 清除状态，AI 恢复
  T5  结束后开了新对话（conversation_id 变化）→ 自动恢复
  T6  普通聊天内容里出现 "new" 不算复活
  T7  重启（新实例、同一存储）后仍拦截
  T8  自定义文案生效
  T9  拿不到对话 ID 时仍拦截（安全兜底）
  T10 AI 调用 block_user → 该用户进入拉黑名单
  T11 被拉黑用户的消息 → 只回拉黑文案，不发给 AI
  T12 被拉黑 / 会话已结束时，指令仍然放行（管理员可解封）
  T13 /unblock 解封后恢复正常
  T14 block_scope=session 只影响当前会话
  T15 注入内容包含正确的用户ID与群号
  T16 管理员同样会被拉黑（不做豁免）
"""

import asyncio
import importlib.util
import os
import sys
import tempfile

ASTROBOT_APP = r"C:\Users\a1234\AppData\Local\AstrBot\backend\app"
sys.path.insert(0, ASTROBOT_APP)

# AstrBot 的 get_astrbot_root() 在非打包模式下默认取当前工作目录，
# 导入 astrbot.core 会顺带创建 <cwd>/data。这里重定向到临时目录，避免污染插件目录。
os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="reject-harass-test-"))

MAIN = r"D:\Users\a1234\Desktop\project\astrbot_end_session\main.py"

spec = importlib.util.spec_from_file_location("main", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NOTICE = "AI可能无法帮助你，请开启新对话"
BLOCKED_NOTICE = "你已被拉黑，无法继续对话"
PLUGIN_ID = "celraw/astrbot_plugin_reject_harassment"

# ---------------- 假存储 ----------------
STORE: dict[tuple[str, str, str], object] = {}


class FakeSP:
    async def get_async(self, scope, scope_id, key, default=None):
        return STORE.get((scope, scope_id, key), default)

    async def put_async(self, scope, scope_id, key, value):
        STORE[(scope, scope_id, key)] = value

    async def remove_async(self, scope, scope_id, key):
        STORE.pop((scope, scope_id, key), None)


mod.sp = FakeSP()


# ---------------- 假 event ----------------
class FakeResult:
    def __init__(self, text=""):
        self.text = text


class FakeEvent:
    def __init__(
        self,
        umo="aiocqhttp:GroupMessage:100",
        text="",
        handlers=None,
        sender_id="20001",
        sender_name="路人甲",
        group_id="100",
        platform_name="aiocqhttp",
        platform_id="aiocqhttp",
        self_id="90000",
        role="member",
    ):
        self.unified_msg_origin = umo
        self._text = text
        self._extras = {"activated_handlers": handlers or []}
        self.sent = []
        self._force_stopped = False
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._group_id = group_id
        self._platform_name = platform_name
        self._platform_id = platform_id
        self._self_id = self_id
        self.role = role

    def get_message_str(self):
        return self._text

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name

    def get_group_id(self):
        return self._group_id

    def get_self_id(self):
        return self._self_id

    def get_platform_name(self):
        return self._platform_name

    def get_platform_id(self):
        return self._platform_id

    def is_admin(self):
        return self.role == "admin"

    def plain_result(self, text):
        return FakeResult(text)

    async def send(self, result):
        self.sent.append(getattr(result, "text", None))

    def stop_event(self):
        self._force_stopped = True

    def is_stopped(self):
        return self._force_stopped


class FakeCommandFilter:
    pass


FakeCommandFilter.__name__ = "CommandFilter"


class FakeCmdHandler:
    def __init__(self):
        self.event_filters = [FakeCommandFilter()]


class FakeReq:
    def __init__(self, system_prompt="你是助手。"):
        self.system_prompt = system_prompt


# ---------------- 假 context ----------------
class FakeConvMgr:
    def __init__(self, cid="conv-A"):
        self.cid = cid

    async def get_curr_conversation_id(self, umo):
        return self.cid


class FakeContext:
    def __init__(self, cid="conv-A"):
        self.conversation_manager = FakeConvMgr(cid)

    def get_config(self, umo=None):
        return {"disable_builtin_commands": False}


UMO = "aiocqhttp:GroupMessage:100"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


def new_plugin(config=None, cid="conv-A"):
    p = mod.RejectHarassmentPlugin(FakeContext(cid), config=config or {})
    p.plugin_id = PLUGIN_ID
    return p


async def main():
    plugin = new_plugin()

    # T1 未结束时放行 + 注入身份
    req = FakeReq()
    ev = FakeEvent(UMO, "你好")
    await plugin.guard_llm_request(ev, req)
    check("T1 未结束放行", not ev.is_stopped() and ev.sent == [], f"sent={ev.sent}")
    check(
        "T15 注入发送者ID与群号",
        "20001" in req.system_prompt and "100" in req.system_prompt,
        req.system_prompt.replace("\n", " | ")[-80:],
    )

    # T2 AI 调用 end_session
    ev = FakeEvent(UMO, "骂人的话")
    ret = await plugin.end_session(ev, reason="多人骚扰")
    check(
        "T2 结束会话",
        ev.is_stopped() and ev.sent == [NOTICE] and "结束" in ret,
        f"sent={ev.sent}",
    )

    # T3 结束后普通消息
    ev = FakeEvent(UMO, "再问一句")
    await plugin.guard_llm_request(ev, FakeReq())
    check("T3 结束后拦截", ev.is_stopped() and ev.sent == [NOTICE], f"sent={ev.sent}")

    # T12 指令放行（解封入口不能被自己拦掉）
    ev = FakeEvent(UMO, "/unblock 20001", handlers=[FakeCmdHandler()])
    await plugin.guard_llm_request(ev, FakeReq())
    check("T12 结束状态下指令放行", not ev.is_stopped(), f"stopped={ev.is_stopped()}")

    # T6 普通内容含 new 不复活
    ev = FakeEvent(UMO, "new 是啥意思")
    await plugin.guard_llm_request(ev, FakeReq())
    check("T6 普通 new 不复活", ev.is_stopped() and ev.sent == [NOTICE])

    # T4 /new 复活
    ev = FakeEvent(UMO, "/new", handlers=[FakeCmdHandler()])
    async for _ in plugin.revive_on_new(ev):
        pass
    await plugin.guard_llm_request(ev, FakeReq())
    check("T4 /new 复活", await plugin._get_session_state(UMO) is None)

    # T5 新对话恢复
    await plugin.end_session(FakeEvent(UMO, "又被骂"), reason="again")
    p2 = new_plugin(cid="conv-B")
    ev = FakeEvent(UMO, "新对话第一句")
    await p2.guard_llm_request(ev, FakeReq())
    check("T5 新对话自动恢复", not ev.is_stopped() and ev.sent == [])

    # T7 重启后仍拦截
    plugin7 = new_plugin()
    await plugin7.end_session(FakeEvent(UMO, "第三回"), reason="persist")
    p7b = new_plugin()
    ev = FakeEvent(UMO, "重启后还理我吗")
    await p7b.guard_llm_request(ev, FakeReq())
    check("T7 重启后仍拦截", ev.is_stopped() and ev.sent == [NOTICE])

    # T9 无对话 ID 仍拦截
    class NoCidCtx(FakeContext):
        def __init__(self):
            super().__init__()
            self.conversation_manager = self

        async def get_curr_conversation_id(self, umo):
            return None

    p9 = mod.RejectHarassmentPlugin(NoCidCtx(), config={})
    p9.plugin_id = PLUGIN_ID
    ev = FakeEvent(UMO, "对话ID拿不到")
    await p9.guard_llm_request(ev, FakeReq())
    check("T9 无对话ID仍拦截", ev.is_stopped() and ev.sent == [NOTICE])

    # T8 自定义文案
    p8 = new_plugin({"notice_text": "我不想聊了"})
    ev = FakeEvent(UMO, "hi")
    await p8.end_session(ev, reason="自定义")
    check("T8 自定义文案", ev.sent == ["我不想聊了"], f"sent={ev.sent}")

    # ---- 拉黑相关 ----
    STORE.clear()
    pb = new_plugin()

    # T10 AI 拉黑用户
    ev = FakeEvent(UMO, "辱骂", sender_id="30001", sender_name="骚扰者")
    ret = await pb.block_user(ev, user_id="", reason="持续辱骂")
    blocklist = await pb._load_blocklist()
    check(
        "T10 拉黑用户",
        ev.is_stopped() and ev.sent == [BLOCKED_NOTICE] and len(blocklist) == 1,
        f"blocklist={list(blocklist)}",
    )

    # T11 被拉黑用户消息不进 AI
    ev = FakeEvent(UMO, "我继续骂", sender_id="30001")
    await pb.guard_llm_request(ev, FakeReq())
    check(
        "T11 拉黑后拦截",
        ev.is_stopped() and ev.sent == [BLOCKED_NOTICE],
        f"sent={ev.sent}",
    )

    # 其他用户不受影响
    ev = FakeEvent(UMO, "你好呀", sender_id="40002", sender_name="正常人")
    await pb.guard_llm_request(ev, FakeReq())
    check("T11b 他人不受影响", not ev.is_stopped() and ev.sent == [])

    # T12 被拉黑用户的指令仍放行（否则无法自救）
    ev = FakeEvent(UMO, "/unblock 30001", handlers=[FakeCmdHandler()], sender_id="30001")
    await pb.guard_llm_request(ev, FakeReq())
    check("T12b 拉黑后指令放行", not ev.is_stopped())

    # T13 解封
    removed = await pb._remove_blocked_by_id("30001")
    ev = FakeEvent(UMO, "我又来了", sender_id="30001")
    await pb.guard_llm_request(ev, FakeReq())
    check("T13 解封后恢复", removed == 1 and not ev.is_stopped(), f"removed={removed}")

    # T16 管理员也会被拉黑
    ev = FakeEvent(UMO, "管理员骂人", sender_id="1", sender_name="管理员", role="admin")
    await pb.block_user(ev, user_id="1", reason="管理员也骂人")
    ev2 = FakeEvent(UMO, "我再说一句", sender_id="1", role="admin")
    await pb.guard_llm_request(ev2, FakeReq())
    check("T16 管理员同样被拉黑", ev2.is_stopped() and ev2.sent == [BLOCKED_NOTICE])

    # T14 session 范围：只影响当前会话
    STORE.clear()
    ps = new_plugin({"block_scope": "session"})
    ev = FakeEvent(UMO, "骂人", sender_id="50005")
    await ps.block_user(ev, user_id="50005", reason="本会话内骚扰")
    # 同会话 → 拦截
    ev_same = FakeEvent(UMO, "同群继续", sender_id="50005")
    await ps.guard_llm_request(ev_same, FakeReq())
    # 另一会话 → 放行
    ev_other = FakeEvent("aiocqhttp:GroupMessage:200", "别的群", sender_id="50005")
    await ps.guard_llm_request(ev_other, FakeReq())
    check(
        "T14 session 范围隔离",
        ev_same.is_stopped() and not ev_other.is_stopped(),
        f"same={ev_same.is_stopped()}, other={ev_other.is_stopped()}",
    )


asyncio.run(main())

failed = [r for r in results if not r[1]]
print(f"\n结果: {len(results) - len(failed)}/{len(results)} 通过")
sys.exit(1 if failed else 0)
