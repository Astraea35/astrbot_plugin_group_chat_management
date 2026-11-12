# 群聊管理

面向 AstrBot 的 QQ 群管理插件，支持 AI 入群审核、广告识别、图片文字与二维码识别，以及违规处理后的管理员提醒。

基于 [zhaisir 的 QQ群智能守护者](https://github.com/zcj-ui/astrbot_plugin_group_guardian) v2.8.11 维护，保留原作者署名与 MIT 许可证。本项目为独立维护版本，原始基线提交为 `569f6bb1a0dd0bc77f2085ba8be9bdac72be3de5`。

## 安装

要求 AstrBot >= 4.24.2，使用 OneBot / aiocqhttp 平台，机器人拥有目标群的管理员权限。

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

## 配置

请先阅读插件中的免责声明，再自行确认并开启相关功能。配置仅在自己的 AstrBot 实例中保存。

| 配置项 | 作用 |
| --- | --- |
| `moderation_llm_provider_id` | 文字审核使用的 AstrBot 模型提供商 |
| `ocr_provider_id` | 支持图片输入的模型；同一模型支持文字和图片时可设为相同提供商 |
| `join_empty_auto_accept` | 是否允许空白入群申请自动通过，仍受等级门槛约束 |
| `join_min_qq_level` | 自动通过所需的真实 QQ 最低等级；0 表示不设等级门槛 |
| `moderation_require_ai_confirmation` | 处罚前是否必须经 AI 确认 |
| `moderation_notify_private_enabled` | 是否开启处理后的管理员私聊提醒 |
| `moderation_notify_admin_id` | 接收提醒的管理员 QQ，默认留空 |
| `moderation_notify_at_fallback` | 私聊失败时是否在群内 @该管理员 |

请同时配置目标群范围、审核开关、管理员豁免和处罚时长。自行设置入群关键词及 AI 标准，结合群聊语境区别实际引流与正常交流、引用举报、玩笑。

## 开发与测试

```shell
python -m pip install pytest pytest-asyncio pytest-subtests pillow numpy opencv-python-headless
python -m pytest tests/test_membership_api_result.py tests/test_provider_streaming.py tests/test_group_review_policy.py tests/test_group_policy_revision.py -q
```

测试使用虚构账号、群号和合成样例，不连接实际 QQ 账号，也不会执行真实处罚。`lexicon.db` 是与原项目一致的静态词库，不含实例审核记录。

本仓库不包含运行配置、密钥、聊天记录、审核数据库、NAS 路径或私人 Git 历史。

## 许可证

MIT。原作者：zhaisir；本维护版：Astraea35。详见 [LICENSE](LICENSE)。
