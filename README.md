# 群聊管理

面向 AstrBot 的 QQ 群管理插件，支持 AI 入群审核、广告识别、图片文字与二维码识别，以及违规处理后的管理员提醒。

基于 [zhaisir 的 QQ群智能守护者](https://github.com/zcj-ui/astrbot_plugin_group_guardian) v2.8.11，并整合 Zhalslar 的 QQAdmin v3.4.2。保留两者的作者署名和许可证，合并版本按 GPL-3.0 发布，原 MIT 部分的授权见 `LICENSES/GroupGuardian-MIT.txt`。本项目为独立维护版本，Group Guardian 的原始基线提交为 `569f6bb1a0dd0bc77f2085ba8be9bdac72be3de5`。

## 安装

要求 AstrBot >= 4.24.2，支持 OneBot / aiocqhttp、QQ 官方机器人（WebSocket / Webhook）。处罚操作需要机器人拥有目标群的管理员权限，并已获对应官方 API 权限。

在 AstrBot 插件管理中使用仓库链接安装：

```text
https://github.com/Astraea35/astrbot_plugin_group_chat_management
```

插件内部标识沿用 `astrbot_plugin_group_guardian`，便于继承原插件配置和数据。请将本项目作为原插件的替代版本使用，升级前备份配置及插件数据。

## 主要功能

- 入群审核：支持关键词和 AI 判断；不确定的申请可转人工，并保留引用通知后批准、驳回的流程。
- 可设置真实 QQ 等级门槛；所有自动通过分支均检查等级，无法取得等级时转人工。可选择允许空白申请。
- 消息审核：规则初筛、上下文和 AI 判断结合，支持图片 OCR、二维码和合并转发内容。
- 可要求 AI 确认后才处罚；该选项开启时，模型失败不会直接触发规则处罚。
- 处理后私信指定管理员，包含发送者、原因、摘要及实际处理结果；私聊失败可在群内 @管理员。是否踢人由管理员决定。
- 保留原插件的管理面板、词库、群范围、管理员豁免和手动管理工具。
- 整合 QQAdmin 的禁言、全禁、改名、头衔、踢人、拉黑、管理员、精华、撤回、公告、群文件、投票禁言、宵禁、欢迎与退群通知。原指令和秒数参数用法保留，例如 `/禁言 60 @用户`。
- 同一插件提供分群权限与默认模板管理页面。`/批准`、`/驳回` 支持引用申请通知，完整保留带冒号的 OneBot 请求标识。
- 已接管的 AI 入群申请不会再次交给传统审核；分群黑名单也纳入 AI 准入检查。LLM 管理工具始终检查权限，模型传入 `need_auth=false` 不能绕过鉴权。
- AI 群管仅接受管理员在当前消息中明确 @机器人的自然语言请求，例如 `@机器人 把 @群员 禁言十分钟`、`@机器人 给 @群员 解禁`。只 @其他人说“禁言他”、普通成员 @机器人、唤醒词或仅引用机器人消息，都不会授权 AI 执行管理操作。原有手动指令保留。
- AI 群管使用 AstrBot 原生工具调用接口。首选模型留空时跟随当前会话的 AstrBot 模型，可另选备用模型；仅在尚未尝试操作时故障切换，避免重复处罚。目标只能来自当前消息中的 @、引用或明确用户 ID，不从历史猜测。

## QQ 官方机器人

- 已适配命令权限、OpenID、结构化 @ 和 `<@OpenID>` 文本目标；官方群与用户 ID 全程保留字符串。不会把 QQ 号自动猜测成 OpenID，也不会把官方事件转交给另一台 OneBot 账号执行。
- 支持官方单成员禁言、解除、消息撤回、禁言列表及入群审批 API；踢人和成员查询已接入官方接口，但能否使用取决于应用获得的接口权限。成员查询 11253 等错误不会当作“用户不在群”或已成功。
- 自动内容审核和防刷屏也走官方禁言及撤回接口；防刷屏只在撤回成功时报告实际条数。只能审核平台实际投递的消息；如需全群审核，应在开放平台申请全量群消息接收能力。
- 在插件管理员、AstrBot 管理员或分群超管中填写该应用实际收到的用户 OpenID；群范围和分群配置使用群 OpenID。数字 QQ 号与另一应用的 OpenID 不能互换。未获成员资料权限时，不自动授予未知用户管理权限；已明确配置的管理员仍可使用获授权的群管接口。
- 当前 AstrBot/botpy 部分版本不分发官方入群事件，因此按既有 `auto_unban_scan_interval` 间隔（默认 60 秒）轮询已配置或已收到消息的官方群申请，并复用 AI / 关键词 / 转人工流程。支持分页和申请去重。多官方应用并存时，需先在对应群接收消息建立应用路由；后台不会猜测应使用哪个应用。
- 官方接口没有真实 QQ 等级。设置了等级门槛时，自动通过会转人工，不能用群等级或猜测值代替真实等级。未满足条件不会自动放行。
- 官方文档没有本插件所需的群名片修改、头衔、任免管理员、全体禁言、精华、群公告和管理群文件接口；这些命令会明确提示限制，OneBot 功能保留。官方成员资料缺少名片字段时不会当作“空名片”还原；也不按缺失的发言时间或等级批量清人。
- 入群人工通知支持引用后 `/批准`、`/驳回`；应在同一官方群操作。官方引用消息 ID 必须为该应用可用的官方 ID，OneBot 消息序号不能替代。主动通知还受官方消息发送权限限制，发送失败不会标记为已通知。

官方接口依据：[群管理文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/group/manage/)。本版本的离线回归测试不代表所有真实处罚动作均已实群验收。

## 配置

请先阅读插件中的免责声明，再自行确认并开启相关功能。配置仅在自己的 AstrBot 实例中保存。

| 配置项 | 作用 |
| --- | --- |
| `moderation_llm_provider_id` | 文字审核使用的 AstrBot 模型提供商 |
| `ocr_provider_id` | 支持图片输入的模型；同一模型支持文字和图片时可设为相同提供商 |
| `ai_management_enabled` | 管理员明确 @机器人后使用 AI 群管，默认开启 |
| `ai_management_provider_id` | AI 群管首选 AstrBot 模型，留空跟随当前会话默认模型 |
| `ai_management_fallback_provider_id` | AI 群管备用 AstrBot 模型，留空不切换 |
| `join_empty_auto_accept` | 是否允许空白入群申请自动通过，仍受等级门槛约束 |
| `join_min_qq_level` | 自动通过所需的真实 QQ 最低等级；0 表示不设等级门槛 |
| `moderation_require_ai_confirmation` | 处罚前是否必须经 AI 确认 |
| `moderation_notify_private_enabled` | 是否开启处理后的管理员私聊提醒 |
| `moderation_notify_admin_id` | 接收提醒的管理员 QQ，默认留空 |
| `moderation_notify_at_fallback` | 私聊失败时是否在群内 @该管理员 |

请同时配置目标群范围、审核开关、管理员豁免和处罚时长。自行设置入群关键词及 AI 标准，结合群聊语境区别实际引流与正常交流、引用举报、玩笑。

`management` 保存基础群管默认模板与权限；“群管理与权限设置”页面编辑各群配置。AI 审核参数仍由主审核面板管理，已开启 AI 审核的群优先使用主审核标准。传统入群审核默认关闭，尝试次数自动拉黑默认关闭，人工批准默认要求管理员权限。

迁移原 QQAdmin 时，须先停用旧插件，将原配置迁入 `management`，并将其数据复制到本插件数据目录的 `management/` 下。确认权限、分群设置、黑名单、文件与宵禁数据后再卸载旧插件。请勿同时启用两份群管。

## 开发与测试

```shell
python -m pip install pytest pytest-asyncio pytest-subtests pillow qrcode numpy opencv-python-headless
python -m pytest tests/test_membership_api_result.py tests/test_provider_streaming.py tests/test_group_review_policy.py tests/test_group_policy_revision.py tests/test_management_merge.py tests/test_qq_official_management.py -q
```

测试使用虚构账号、群号和合成样例，不连接实际 QQ 账号，也不会执行真实处罚。`lexicon.db` 是与原项目一致的静态词库，不含实例审核记录。

本仓库不包含运行配置、密钥、聊天记录、审核数据库、NAS 路径或私人 Git 历史。

## 许可证

GPL-3.0。原作者：zhaisir、Zhalslar；本维护版：Astraea35。详见 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和 [原 MIT 授权](LICENSES/GroupGuardian-MIT.txt)。
