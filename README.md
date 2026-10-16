<div align="center">

# 拒绝 AI 骚扰

**让 AI 也有脾气 —— 被骚扰时,它可以拉黑你。**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.5-ff69b4.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.1.2-green.svg)](metadata.yaml)

*AstrBot 插件 · 单人骚扰就拉黑,多人骚扰就结束会话*

</div>

---

## 这是什么

大多数机器人面对辱骂只能默默忍受。这个插件给了 AI 一个「拒绝」的权利:

- 有人**持续辱骂**?→ AI 调用 `block_user` **精准拉黑这个人**,其他人不受影响。
- **多个人一起骚扰**?→ AI 调用 `end_session` **结束整个会话**。

被拒绝之后,骚扰者的消息**根本不会发送给 AI**,只会收到一句冷冰冰的回复。
只有开新对话(`/new`)才能重新开始。

> 核心保证:**消息在真正发给模型之前就被拦截**,不是"让 AI 装作没看见"。

---

## 功能一览

| | 工具 | 适用场景 | 效果 |
|:--|:--|:--|:--|
| 🚫 | `block_user(user_id, reason)` | **某一个人**持续骚扰 | 只拉黑这个人,其他人照常聊天 |
| 🔒 | `end_session(reason)` | **多人**同时骚扰 / 群已无法交流 | 整个会话结束,该聊天不再有 AI |

### 行为对照

| 情况 | 机器人表现 |
| :-- | :-- |
| 用户被拉黑 | 消息不进 AI,只回复 `你已被拉黑，无法继续对话` |
| 会话被结束 | 任何消息不进 AI,只回复 `AI可能无法帮助你，请开启新对话` |
| 会话结束 → 发送 `/new` | 清除状态,AI 恢复对话 ✅ |
| 用户开了新对话 | 自动识别并恢复(兜底) |
| **任何指令** | **永远放行**,不受拉黑/结束影响 |

---

## 亮点设计

### 🎯 注入用户身份,拒绝误拉黑

AI 如果只知道「有人在骂我」,是分不清群里哪一个人的。插件会把当前发言者的身份
随本次请求带给 AI:

```
【当前消息发送者信息】
- 昵称：路人甲
- 用户ID：20001
- 平台：aiocqhttp
- 群号：1059396870
- 若需要拉黑正在骚扰你的用户，请使用上面的「用户ID」调用 block_user
```

这样 AI 拉黑的是**具体的人**,而不是"碰巧在说话的人"。

#### ⚡ 为什么不用 system_prompt

**绝对不能**把它写进 `req.system_prompt`。群聊里每条消息的发送者都不同,
改了 system_prompt 就等于每次都让提示词前缀失效,**直接打掉 LLM 的提示词缓存命中率**。

正确做法是用官方的临时内容块 API:

```python
from astrbot.core.agent.message import TextPart

req.extra_user_content_parts.append(
    TextPart(text=block).mark_as_temp()   # 仅本次请求生效，不写入历史
)
```

这些内容会拼进**用户消息**的 content blocks,**系统提示词保持完全不变**,
前缀稳定 → 缓存命中不受影响。`mark_as_temp()` 会置 `_no_save`,
因此也不会被持久化进对话历史。

### 🛡️ 指令永远放行 —— 不会被锁死

这里有个容易踩的坑:指令执行完之后,消息管道**仍然可能继续走到 LLM 请求**。
如果拦截逻辑不区分指令,`/unblock` 自己就会被拦掉,结果**谁都解不了封**。

所以拦截钩子第一件事就是放行指令:

```python
# 指令永远放行：管理员可以借此解封，机器人也不会被指令卡死
if self._is_command_event(event):
    return
```

### 👔 管理员的定义

解封指令要求 **AstrBot 管理员**(WebUI 里 `admins_id` 配置的用户),
判定依据是 `role == "admin"`,而该值**只由 `admins_id` 决定**。

**群聊管理员不算**,不会获得解封权限。

### ⚖️ 管理员也会被拉黑

`block_user` **不对管理员做豁免** —— AI 在群里面对所有人一视同仁。
但因为指令永远放行,管理员随时可以用 `/unblock` 把自己放出来。

### 💾 状态持久化

会话状态与拉黑名单通过 AstrBot 的 `sp`(SharedPreferences)持久化,
**重启后依然有效**。全局拉黑按 `平台:用户ID` 记录,会话级拉黑按会话隔离。

---

## 安装

### 方式一:下载安装包(推荐)

前往 [**Releases**](https://github.com/Celraw/astrbot_plugin_reject_harassment/releases/latest)
下载 `astrbot_plugin_reject_harassment-x.y.z.zip`,在 WebUI 的「插件」页面
选择**从文件安装**,然后重载插件。

### 方式二:手动安装

```bash
cd <AstrBot>/data/plugins
git clone https://github.com/Celraw/astrbot_plugin_reject_harassment.git
```

然后在 WebUI 的「插件」页面点击重载。

> 目录名必须与 `metadata.yaml` 中的 `name` 一致:
> `astrbot_plugin_reject_harassment`

**无第三方依赖**,不需要 `requirements.txt`。

---

## 使用

装好之后**不需要任何配置**,AI 会在被骚扰时自行判断该用哪个工具。

### 管理指令

| 指令 | 权限 | 说明 |
| :-- | :-- | :-- |
| `/unblock <用户ID 或 @某人>` | AstrBot 管理员 | 解封指定用户 |
| `/blocklist` | AstrBot 管理员 | 查看当前拉黑名单 |
| `/block <用户ID 或 @某人> [原因]` | AstrBot 管理员 | 手动拉黑用户 |

> 解封时既可以直接写数字 ID，也可以在群里 **@ 那个人**，两种都支持。

---

## 配置

在 WebUI 的插件配置页中修改:

| 配置项 | 类型 | 默认值 | 说明 |
| :-- | :-- | :-- | :-- |
| `notice_text` | string | `AI可能无法帮助你，请开启新对话` | 会话结束后的固定文案 |
| `blocked_notice_text` | string | `你已被拉黑，无法继续对话` | 被拉黑用户收到的文案 |
| `notify_on_end` | bool | `true` | 结束会话时是否立即回复 |
| `notify_on_block` | bool | `true` | 拉黑用户时是否立即回复 |
| `block_scope` | string | `global` | `global` = 所有会话都拉黑;`session` = 只在当前会话 |
| `inject_sender_info` | bool | `true` | 是否把发送者昵称/ID/群号注入给 AI |

---

## 工作原理

```
收到消息
   │
   ▼
WakingCheck ─ whitelist ─ 限流 ─ 内容安全 ─ 预处理
   │
   ▼
ProcessStage
   ├── 插件 handler（指令在这里执行）
   │
   └── 是否调用 LLM？
          │
          ▼
     ┌───────────────────────────────────────┐
     │  @filter.on_llm_request  priority=10000│
     │                                        │
     │  ① 是指令？ ──────── 是 ──▶ 放行 ✅     │
     │  ② 用户被拉黑？ ──── 是 ──▶ 回文案 + 拦截│
     │  ③ 会话已结束？ ──── 是 ──▶ 回文案 + 拦截│
     │  ④ 注入发送者身份 ──▶ 正常交给 LLM      │
     └───────────────────────────────────────┘
```

命中拦截时回复文案并 `event.stop_event()`,`call_event_hook` 随即返回 `True`,
内部 Agent 直接 `return` —— **LLM 完全不会被调用**。

### 挂载点

| 装饰器 | 作用 |
| :-- | :-- |
| `@filter.llm_tool` | 注册 `block_user` / `end_session` 两个工具 |
| `@filter.on_llm_request(priority=10000)` | 请求发出前的最后一道关卡 |
| `@filter.command("new", priority=1000)` | 抢在内置 `/new` 之前清除结束状态 |
| `@filter.permission_type(filter.PermissionType.ADMIN)` | 管理指令权限 |

---

## 测试

```bash
python test_end_session.py
```

测试脚本不写死任何本机路径:被测文件按脚本所在目录解析,
AstrBot 的 `astrbot` 包未装进 `site-packages` 时用环境变量指定即可:

```bash
# Windows
set ASTRBOT_APP=D:\path\to\AstrBot\backend\app
# Linux / macOS
export ASTRBOT_APP=/path/to/AstrBot/backend/app
```

离线测试,不需要启动 AstrBot,覆盖 **24 个用例**:

```
PASS  T1  未结束放行
PASS  T15 注入发送者ID与群号
PASS  T2  结束会话
PASS  T3  结束后拦截
PASS  T12 结束状态下指令放行
PASS  T6  普通 new 不复活
PASS  T4  /new 复活
PASS  T5  新对话自动恢复
PASS  T7  重启后仍拦截
PASS  T9  无对话ID仍拦截
PASS  T8  自定义文案
PASS  T10 拉黑用户
PASS  T11 拉黑后拦截
PASS  T11b 他人不受影响
PASS  T12b 拉黑后指令放行
PASS  T13 解封后恢复
PASS  T16 管理员同样被拉黑
PASS  T14 session 范围隔离
PASS  T17 @某人 解封
PASS  T18 渲染文本解封
PASS  T19 工具不误取At
PASS  T20 纯数字解封
PASS  T21 不改 system_prompt（保护提示词缓存）
PASS  T21b 以临时内容块注入（不写入历史）

结果: 24/24 通过
```

---

## 常见问题

<details>
<summary><b>群聊管理员能解封吗?</b></summary>

不能。权限依据 AstrBot 的 `admins_id`(WebUI 配置的机器人管理员),
群聊管理员不会被设为 `admin`。
</details>

<details>
<summary><b>被拉黑后发 <code>/new</code> 能解封吗?</b></summary>

不能。`/new` 只清除**会话结束**状态,不解除**用户拉黑**。
拉黑必须由管理员用 `/unblock` 解除。
</details>

<details>
<summary><b>为什么群里没 @ 机器人时不回复?</b></summary>

这类消息本来就不会进入 AI 链路,拦截钩子不会触发,所以也不会回复。
这符合平台习惯,避免机器人在群里刷屏。
</details>

<details>
<summary><b>AI 会不会误判、把正常人拉黑?</b></summary>

工具描述里明确要求 AI 依据注入的「用户ID」判断,不要凭猜测填写。
若仍误判,管理员可用 `/blocklist` 查看、`/unblock` 解封。
也可以把 `notify_on_block` 打开,让每次拉黑都有明确回执便于察觉。
</details>

<details>
<summary><b>会话结束和拉黑有什么区别?</b></summary>

- **拉黑**:针对**人**,该用户在哪都被拒绝,其他人正常。
- **结束会话**:针对**整个聊天**,所有人都不再有 AI,直到 `/new`。

单人骚扰用前者,多人骚扰用后者。
</details>

---

## 更新日志

### v1.1.2

- ⚡ **改为用官方临时内容块注入发送者信息,不再改动 `req.system_prompt`**。
  群聊里每条消息的发送者都不同,原来往 system_prompt 里追加动态内容会让
  提示词前缀每次都变,**严重打击 LLM 提示词缓存命中率**。
  现在改用 `req.extra_user_content_parts` + `TextPart(...).mark_as_temp()`:
  内容拼进用户消息块,系统提示词保持完全不变,且不写入对话历史。
- 🧹 测试脚本移除本机硬编码路径,改用脚本相对目录 + `ASTRBOT_APP` 环境变量。
- ✅ 新增 T21/T21b 两个用例,锁住"不改 system_prompt"这一行为。

### v1.1.1

- 🐛 **修复:`/unblock @某人` 解封失败**。
  之前只认纯数字 ID,`@某人` 会被解析成 `@昵称(数字)` 这样的文本,
  与名单里存的纯数字对不上,导致「不在拉黑名单中」。
  现在会正确读取消息链中的 At 段,并兼容被平台渲染过的文本。
- 🐛 工具端 `block_user` 特意**不**回退到消息里的 @ 目标 ——
  群里发消息时 @ 的通常是机器人自己,否则会把 bot 的 ID 当成骚扰者。

### v1.1.0

- 首次发布:AI 可主动拉黑单个用户(`block_user`)或结束整个会话(`end_session`)。

---

## 许可

[MIT](LICENSE) © 2026 Celraw
