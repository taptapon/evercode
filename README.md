# Evercode

> 抽身离开。
>
> Claude Code 继续编码。
>
> 回来时，等待你的是规划好、已提交、已测试、已评审的工作。

Evercode 是一个 Claude Code 技能，能把普通的 Claude Code 会话变成自主、全天候的开发代理。你只需批准一件事——一个**目标**——然后离开。代理会在功能分支上规划、实现并提交工作，最长可达 8 小时。

当 OpenAI Codex 可用时，每次提交都要经过第二个、用独立提示词的 LLM 的独立评审。当它不可用时，代理会回退到明确标记的自评审并继续工作——你会失去双 LLM 保障，但工作循环不停。

```
═══════════════════════════════════════════════════════════════
  ⚙️  EVERCODE ENGAGED  ⚙️
═══════════════════════════════════════════════════════════════

  你现在可以离开了。剩下的我来。

  Run ID:      2026-04-19-2318
  Branch:      evercode/harden-api-errors (来自 d9aad96)
  Objective:   加固 API 层的错误处理——
               统一错误响应、给出站 HTTP 加重试、
               并用测试覆盖缺口。
  Max runtime: 8 小时（或直到 Codex 和我都认为完成了）
  Handoff:     .evercode/runs/2026-04-19-2318/handoff.md

  Evercode keeps coding. ⚙️
═══════════════════════════════════════════════════════════════
```

<a href="https://www.loom.com/share/6bcfdd2579c74de5bdad595c686fa547" target="_blank" rel="noopener noreferrer"><img src="https://github.com/user-attachments/assets/1a95844b-51ca-4944-8918-2a49c3f3e83a" alt="在 Loom 上观看 Evercode 演示"></a>


## 为什么

长程自主编码代理以可预见的方式失败：它们虚构进度、掩饰失败的测试、把范围升级成无关的重构、有时还会推送到 `main`。大多数"让它无人值守运行"的方案，本质就是一个 LLM 给自己的作业打分。

Evercode 做了三个结构性押注：

1. **两个独立的 LLM 互相评审，绝不让同一个给自己打分。** Claude 写代码；Codex（可用时）做对抗式评审。在磁盘上存在真实的评审产物之前，一个任务不能被提交——一个被压缩过、健忘的代理无法悄悄跳过这道关卡。
2. **小而可逆的单元。** 每个任务就是一个提交。一次失败的评审或测试只回滚该任务——而不是整个会话。分支上的每个提交都通过测试。
3. **代理不能自己决定完工。** 以"我们完成了"收尾，需要代理主动提议**并且** Codex 同意。没有 Codex 时，班次会一直运行到 8 小时硬上限。

## 工作原理

每个任务的流程：Claude 规划 → Claude 实现 → Codex 评审代码（循环直到干净，10 轮后硬停并回滚）→ 测试必须通过 → 文件门控提交。预提交关卡会拒绝暂存那些 `code-review.txt` 缺失、为空、或与状态中记录的判定不符的任务。

工作被组织为：

- **目标** —— 你批准的那一件事。
- **关键结果** —— 代理迭代提出的具体交付物，每一个都由 Codex 把关。
- **任务** —— 每个关键结果之下、可独立提交的单元。

只有当代理明确提议"我们完成了"、并且 Codex 在重新评审完整的目标与关键结果历史后同意时，代理才会结束班次。Codex 反复拒绝新提案，是触发这条收尾路径的信号——绝不能替代它。

## 前置要求

- **Claude Code**，以 `--dangerously-skip-permissions` 启动，并在你信任的目录中运行。没有它，自主循环会卡在权限提示上。
- **Git**（推荐）。在 git 仓库之外，Evercode 以优雅降级模式运行——没有提交、没有回滚、没有漂移保护。
- **OpenAI [Codex CLI](https://github.com/openai/codex)**（推荐）。存在时，你获得双 LLM 评审循环和"基于共识收尾"的能力。缺失时，Claude 用每个产物上显式的 `CODEX UNAVAILABLE` 标记做自评审，交接文档会显著标出这些任务，班次运行到 8 小时上限。

## 安装

在 Claude Code 内：

```
/plugin marketplace add taptapon/evercode
/plugin install evercode@evercode
```

或直接克隆到你的技能目录：

```bash
git clone https://github.com/taptapon/evercode.git ~/.claude/skills/evercode
```

或：如果你已在本地有仓库，直接复制进去（会解开 symlink、排除开发期垃圾）——适合无需联网、本地迭代：

```bash
./install-local.sh                       # → ~/.claude/skills/evercode
./install-local.sh ~/proj/.claude/skills # → 某个项目的技能目录
```

重启 Claude Code（或开启新会话）。技能会自动注册。

## 快速开始

在你想要处理的项目里，以 `--dangerously-skip-permissions` 启动 Claude Code。确保你在一个功能分支上（如果你在 `main` 上，Evercode 会提议创建一个），并且工作区是干净的。然后：

```
/evercode
```

技能会一次问你一个问题：绕过权限确认、分支选择、未提交改动的处理方式，以及一个**目标**（或输入 `propose`，让代理根据会话上下文建议一个）。你确认后，`EVERCODE ENGAGED` 横幅触发，班次时钟开始计时——8 小时上限从横幅触发时算起，不是从你输入 `/evercode` 算起。

想提前停止：说 `stop evercode`。在一个活动班次中再次触发，你会得到 停止 / 恢复 / 放弃 的提示。

## 回来时

权威的交接产物是：

```
.evercode/runs/<RUN_ID>/handoff.md
```

它汇总了每个关键结果交付了什么、每个任务的提交哈希、Codex 评审轮数、代理在未经你同意下做出的决定，以及需要人工关注的事项。像评审任何其他 PR 一样评审这个分支。

Evercode 从不运行 `git push`，也从不打开 PR。发布什么由你决定。

## 运行目录

```
.evercode/runs/<RUN_ID>/
├── state.json           运行的真相来源
├── handoff.md           面向人类的摘要
└── key-results/<KR>/
    ├── codex-approval.txt          这个 KR 值得做吗？
    ├── decomp-adversarial.txt      这些是正确的任务吗？
    └── tasks/<T>/
        └── code-review.txt         代码干净吗？
```

每一个判定都是磁盘上真实存在的文件。以往的运行永不修改。

## 安全模型


| 失败模式 | Evercode 如何防止 |
| -------------------------------------- | ---------------------------------------------------------------------------- |
| 代理提交了损坏的代码              | 每次提交前运行完整测试套件；失败触发作用域内的回滚   |
| 代理为了"省时间"跳过代码评审 | 预提交关卡验证评审文件存在，并与记录的判定匹配     |
| 代理给自己的作业打分          | Codex 是独立进程；自评审（需要时）被显式标记 |
| 代理过早决定完工    | 需要双重共识——Codex 必须同意，否则触发 8 小时上限                |
| 代理无限运行下去                | 8 小时硬上限，并在当前任务上原子地收尾                     |
| 你自己的工作被覆盖           | 每次写入前做漂移检查；外部 HEAD 变更会停止运行           |
| 失败的任务留下脏仓库          | 按任务作用域回滚到该任务的 `start_commit`                        |
| 代理推送到远程                 | 该技能从不调用 `git push`                                             |
| 范围蔓延、超出目标          | 每个关键结果都基于"这是否服务于目标？"来把关                   |


完整规则集见 [`INVARIANTS.md`](INVARIANTS.md)；完整执行规范见 [`SKILL.md`](SKILL.md)。

## 命令


| 触发方式                                                          | 动作                                              |
| ---------------------------------------------------------------- | --------------------------------------------------- |
| `/evercode`                                                   | 开始一个班次，或对活动班次做 停止 / 恢复 / 放弃 |
| `start evercode`、`keep coding`、`take over` | 同 `/evercode`                              |
| `stop evercode`、`end evercode`、`wrap up`                 | 对活动班次运行结束流程           |


## 局限

- **挂钟时间，而非计算时间。** 8 小时上限度量的是流逝的挂钟时间。如果你的机器进入睡眠，那也算在内。若这点对你很重要，请使用 `caffeinate` 或禁用"交流电源时睡眠"。
- **班次中途不会有用户提问。** 歧义由代理自行解决，并记录在 `state.json.decisions_made` 里——请在交接文档中复核。
- **每个仓库单一会话。** 对功能分支的外部提交会触发漂移保护，并干净地停止运行。
- **没有 Codex 时，不能提前退出。** 班次会运行到 8 小时上限，每个任务的评审都是显式标记的自评审。

## 可选：在长运行中保持上下文有界

一次多小时班次会累积庞大的对话记录。Evercode 为**挺过**压缩而构建——每个任务都以一次上下文刷新开始，从磁盘重读状态——因此你也可以在任务边界激进地裁剪历史，而不丢失任何东西。仓库为此附带了一个可选的伴侣工具：

```
proxy/   # flush proxy：在每个任务边界裁剪对话，不含 summarizer
```

它位于 Claude Code 的 API 路径上。每次任务提交后，技能会发出一个唯一的哨兵标记；代理丢弃较早的对话轮次，并留下一个指针，告诉代理去重读磁盘（这一点它本来也会做）。循环里没有第二个 LLM，没有后台线程，纯标准库实现。

```bash
./evercode --dangerously-skip-permissions          # 一条命令：启动 proxy + Claude Code
```

启动器会启动 flush proxy（若已在运行则复用），把 Claude Code 指向它，并替你设好 `EVERCODE_FLUSH_PROXY=1`——这样你还顺带跳过了班次前的确认提问。请在你的**项目**目录里运行它；proxy 通过脚本自身位置定位。如果 proxy 起不来，启动器会退化为不带 proxy 启动 Claude Code（绝不指向死端口）。加 `--no-proxy` 可显式跳过。

或者手动分步：

```bash
./proxy/run.sh                                      # 监听 :5589
export ANTHROPIC_BASE_URL=http://127.0.0.1:5589     # 把 Claude Code 指向这里
export EVERCODE_FLUSH_PROXY=1                       # 技能发出哨兵标记
```

`EVERCODE_FLUSH_PROXY` 控制哨兵的发射，所以不运行该代理的用户不会付出任何代价。完整细节、调参与单次哨兵设计见 [`proxy/README.md`](proxy/README.md)。
