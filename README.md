# oh-my-elagent

> 极简、高性能的终端 coding agent。输入 `ela` 即可开始。

```
  ___ _    _
 / __| |  /_\
 | (__| |_/ _ \
 \___|____/_/ \_\     oh-my-elagent  v0.1.0
                      cwd  ~/proj
                      /help  /login  /model
                      shift+tab permissions · /effort depth
──────────────────────────────────────────────────────────────────────────────
❯ refactor the parser

The parser now handles nested blocks.
  ● read src/parser.py · 3ms
    src/parser.py (lines 1-40 of 120)
  ● pwsh pytest -q · 1.4s · exit 0
  ✓ 4.2s · 1.8k↑ 240↓ · ctx 12% · 2 steps
──────────────────────────────────────────────────────────────────────────────
❯
  ready · deepseek-chat · Ask Mode · high · ctx 12%    PgUp/PgDn · ctrl+j · ctrl+d
```

`oh-my-elagent` 只做四件事：**流式对话、调用工具、保存会话、保持安静**。

- 核心提示词 + 6 工具：**867 / 1200 tokens**；ultracode + AutoReview 全量 **974 / 1500**
  （`ela --tokens` 可复验）
- 全屏 TUI（alternate screen）：左上角 ELA ASCII logo、主界面无背景填充、可滚动
  transcript、输入、footer 状态栏；弹层固定在输入栏上方，不填充背景
- `/login` 内置常用端点（OpenAI、DeepSeek、OpenRouter、Groq、Mistral、xAI、
  Together、SiliconFlow、Moonshot、GLM、Qwen）与本地 Ollama / LM Studio / llama.cpp；
  登录后自动拉取模型，`↑/↓` 选择、`Enter` 应用
- `/goal <task>` 单次目标：先列 task 清单，再逐项完成，全部完成后才输出结果；
  普通输入不会触发规划
- 默认 6 个工具，输出全部有界：`read` `write` `edit` `pwsh` `search` `askuser`
- 思考深度 `/effort` + ultracode 并行子 agent；goal + ultracode 时每个 task 一个子 agent
- 权限模式：Auto / Ask / Readonly / AutoReview，`shift+tab` 切换
- AutoReview：在 Ask 基础上先让 JSON 安全审查子 agent 判断；安全则自动放行，不安全才询问用户
- 界面参考 [Pi](https://pi.dev/)：单行工具事件、代码块边框、状态行、slash 补全
- OpenAI Chat Completions 协议，可接任何兼容端点（OpenAI、DeepSeek、OpenRouter、本地模型）
- 会话自动保存为 JSONL

## 安装

需要 Python 3.10+ 和 **PowerShell 7**（shell 工具的唯一后端）。默认优先使用
**PowerShell 7 preview**，未安装时回退到 stable 7；`ELA_PWSH` 可以强制指定任意一个：

```powershell
winget install Microsoft.PowerShell
pwsh -v
```

macOS / Linux：

```bash
# macOS
brew install --cask powershell
# Ubuntu / Debian
snap install powershell --classic
```

查找顺序：`ELA_PWSH` → `pwsh-preview`（PATH / `7-preview` / WindowsApps）→
stable `pwsh`（PATH / `7` / WindowsApps）。找不到时 `pwsh` 工具会返回明确的
安装提示，agent 的其他工具不受影响。

```bash
cd oh-my-ELA
python -m pip install -e .
ela --version
```

如果 `ela` 不在 PATH，把 Python 的 `Scripts` 目录加入 PATH，或使用：

```bash
python -m ela
```

Windows 上本机已放置入口脚本，且两个 cmd/PowerShell 入口位于用户 PATH 中：

- `%APPDATA%\npm\ela.cmd`（cmd / PowerShell）
- `%LOCALAPPDATA%\Python\bin\ela.cmd`（备份入口）
- `~/bin/ela`（Git Bash）
- `~/bin/ela.cmd`（备用，`~/bin` 不在 Windows 用户 PATH 中）

它们调用 `.venv/Scripts/python.exe -m ela`，项目移动后需要同步修改。
如果某个已经打开的终端仍提示找不到命令，新开一个终端即可。

## 配置

环境变量优先于 `~/.ela/config.json`：

| 变量 | 说明 |
| --- | --- |
| `OPENAI_API_KEY` / `ELA_API_KEY` | API key |
| `OPENAI_BASE_URL` / `ELA_BASE_URL` | 兼容端点，例如 `https://api.deepseek.com/v1` |
| `ELA_MODEL` | 模型 id |
| `ELA_HOME` | 配置和会话目录，默认 `~/.ela` |
| `ELA_STREAM_USAGE` | 设为 `0` 可关闭 `stream_options.usage` |
| `ELA_PERMISSION` | 启动权限模式：`auto` / `ask` / `readonly` / `autoreview` |
| `ELA_INLINE` | 设为 `1` 强制使用滚动 REPL |
| `ELA_PWSH` | 强制指定 PowerShell 可执行文件（默认优先 7-preview，其次 stable 7） |

```bash
export OPENAI_API_KEY=sk-...
export ELA_MODEL=gpt-4.1-mini
ela
```

PowerShell 中用 `$env:OPENAI_API_KEY = "sk-..."`，其余变量同理。

接入 DeepSeek：

```bash
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export ELA_MODEL=deepseek-chat
export OPENAI_API_KEY=sk-...
ela
```

生成配置模板：

```bash
ela --init
```

```json
{
  "model": "gpt-4.1-mini",
  "context_window": 128000,
  "max_steps": 0,
  "stream_usage": true,
  "verbose": false,
  "timeout": 300.0,
  "permission": "ask"
}
```

`max_steps` 为 `0` 表示不限制单轮工具步数（默认）；设为正整数则在该轮模型调用
次数达到上限时停止并提示。旧配置里的 `max_steps: 40` 仍会生效，用 `/maxstep off`
清除即可。可手动加入 `base_url`、`api_key`、`temperature`、`max_tokens`。
环境变量仍然优先于这里的所有值。

## 使用

```bash
ela                          # 全屏交互模式（默认 Ask Mode）
ela --inline                 # 滚动 REPL（不使用 alternate screen）
ela --permission auto        # Auto Mode 启动
ela --permission autoreview  # AutoReview Mode 启动
ela "fix the failing tests"  # 一次性执行
ela -y "fix the failing tests"  # 一次性执行，Auto Mode
ela --continue               # 继续当前目录最近的会话
ela --resume                 # 选择一个历史会话
ela --json "summarize"       # 机器可读输出
ela --no-save "..."          # 不写会话文件
ela --tokens                 # 打印提示词预算
```

`ELA_INLINE=1` 也可以强制使用滚动 REPL；管道、重定向等非 TTY 环境会自动回退到 inline 模式。
一次性模式默认也是 `ask`，没有交互界面可以审批写/命令；需要自动化写入时请显式使用
`--permission auto`（或 `-y`，或把配置里的 `permission` 设为 `auto`）。

首次使用可以直接运行 `ela`，即使还没有配置模型：

```text
❯ /login
```

登录面板填写请求地址和 API key（key 输入框会遮蔽），保存到 `~/.ela/config.json`。
然后：

```text
❯ /model
```

会请求端点 `GET {base_url}/models`，列出可用模型并在 TUI 中使用方向键选择。
也可以直接 `/model <id>` 指定。

### 全屏界面

在真实终端中，`ela` 默认进入全屏 TUI：

- 左上角：ELA ASCII logo；右侧是版本、cwd 和快捷命令提示
- 中间：可滚动的 transcript，流式 Markdown、工具调用、token/上下文状态
- 底部：多行输入、spinner、当前模型、权限、effort、goal 进度、`ctx` 占用
- 主界面 header/footer/transcript 不填充背景，只使用前景色和细分割线
- 登录、模型选择、effort、权限、askuser 面板都嵌在输入栏正上方：打开时 transcript
  自动让出空间，不覆盖内容，也不填充背景，只用分割线和前景色区分
- slash 补全菜单仍贴近光标显示，使用高对比实底
- 模型标识符已从右上角移除，统一显示在 footer
- 退出后终端内容恢复；会话已保存，可用 `--continue` 恢复

| 按键 | 作用 |
| --- | --- |
| `enter` | 提交输入 |
| `ctrl+j` / `alt+enter` | 输入换行 |
| `ctrl+c` | 生成中取消；空闲时清空输入 |
| `ctrl+d` | 输入为空时退出 |
| `shift+tab` | 循环权限模式：Auto → Ask → Readonly → AutoReview → Auto |
| `PgUp` / `PgDn` | 滚动 transcript |
| `ctrl+o` | 切换工具完整输出 |

`/effort` 会打开思考深度面板：`↑/↓` 选择 low / medium / high，`tab` 切换 ultracode，
`enter` 应用，`esc` 取消。底部 footer 会显示权限、effort、ultracode 和上下文占用。

不适合 alternate screen 的终端，使用 `ela --inline`。

### 交互命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 命令列表 |
| `/login` | 选择端点预设或自定义地址，填写 key 后自动选择模型 |
| `/model [id]` | 无参数时拉取端点模型并选择；带 id 时直接切换 |
| `/effort` | 思考深度面板；`/effort low\|medium\|high`、`/effort ultracode on\|off` |
| `/maxstep` | 查看单轮步数上限；`/maxstep <n>` 设置并保存，`/maxstep off` 取消限制 |
| `/permissions` | 权限模式；`/permissions auto\|ask\|readonly\|autoreview\|cycle` |
| `/goal <task>` | 仅对这一条指令执行 goal 流程：规划 → 执行 → 最终结果 |
| `/clear` / `/new` | 清空上下文并新建会话 |
| `/compact` | 让模型总结历史，释放上下文 |
| `/session` | 会话 id、文件、消息数、token |
| `/resume [id]` | 恢复本目录的历史会话；无 id 时列出 id |
| `/export [file.md]` | 导出为 Markdown |
| `/verbose` | 展开/收起工具完整输出 |
| `/status` | 模型、端点、上下文、提示词预算 |
| `/exit` / `/quit` | 退出 |

`/maxstep` 默认不限制：agent 会一直调用工具直到给出最终回答，或你按 `ctrl+c`
中断。需要防止单轮过长时，用 `/maxstep 80` 设一个上限；达到上限会输出
`stopped after 80 steps` 并结束该轮。设置会写入 `~/.ela/config.json`，
`/maxstep off` 恢复不限制。上限按模型调用轮次计算，一条消息里的多个并行
tool call 只算 1 步。

其他写法：

- `!cmd`：用 pwsh 执行命令，并把输出加入上下文
- `!!cmd`：用 pwsh 执行命令，但不加入上下文

### 登录与模型

- `ela` 在没有 API key / base URL 时也可以启动；footer 显示 `not logged in · /login`。
  云端点只有 `base_url` 没有 key 时同样视为未登录；本地端点（localhost / 127.0.0.1）不需要 key。
- `/login` 分两步，面板固定在输入栏上方：
  1. **选择端点**：`↑/↓` 浏览，`Enter` 下一步，`Esc` 取消。内置
     OpenAI、DeepSeek、OpenRouter、Groq、Mistral、xAI (Grok)、Together、
     SiliconFlow、Moonshot (Kimi)、Zhipu (GLM)、DashScope (Qwen)，
     以及本地 Ollama (`127.0.0.1:11434`)、LM Studio (`127.0.0.1:1234`)、
     llama.cpp (`127.0.0.1:8080`) 和自定义地址。
  2. **填写凭据**：Base URL 已按预设填好，可修改；云端点必须填 key，本地端点和
     自定义端点可以留空。`Tab` 切换字段，`Enter` 连接，`Esc` 返回端点列表。
  - 只接受 `http://` 或 `https://` 地址。
  - `Enter` 后在后台请求 `GET {base_url}/models` 验证凭据，20 秒超时；成功才写入
    `~/.ela/config.json`，失败则保留登录面板并显示原因，旧凭据自动回滚。
  - key 以明文存储，请保护该文件；POSIX 下文件权限为 `0600`。
- 验证成功后打开 `Select model`：`↑/↓` 选择，`Enter` 应用，`Esc` 取消；
  选中的模型写入配置，下次启动直接使用。之后也可用 `/model` 重新拉取。
- `/model` 无参数时同样请求 `GET {base_url}/models`：
  - TUI 弹出模型列表，`↑/↓` 选择，`Enter` 应用，`Esc` 取消。
  - inline 模式打印编号列表，输入编号选择。
  - 通过 `/model` 做的选择是会话级；也可以 `/model <id>` 直接指定。
- 模型标识符不再显示在右上角，统一显示在 footer。
- inline 模式的 `/login` 用编号选择端点，流程相同。

### 权限模式

| 模式 | 读取/搜索 | 写/编辑/pwsh | 文件范围 | 行为 |
| --- | --- | --- | --- | --- |
| Auto Mode | 允许 | 允许 | 所有文件夹 | 不询问 |
| Ask Mode（默认） | 允许 | 需要审批 | 启动目录及子目录 | 写/编辑/pwsh 询问 |
| Readonly Mode | 允许（含 askuser） | 拒绝 | 所有文件夹 | 不询问 |
| AutoReview Mode | 允许 | 安全审查后放行，不安全才询问 | 启动目录及子目录 | JSON 安全子 agent |

- 全屏主界面用 `shift+tab` 循环；inline 模式用 `/permissions`。
- Ask Mode / AutoReview Mode 会对文件工具做路径收敛：路径解析后必须位于启动时的 cwd 内，
  越界直接返回权限错误。
- AutoReview Mode 在 Ask Mode 基础上，每次写/编辑/pwsh 先调用一个安全审查子 agent：
  - 子 agent 使用同一模型、独立上下文、无工具，只返回 JSON：
    `{"safe": true|false, "reason": "..."}`
  - `safe=true` 时自动放行主 agent，并在 transcript 显示 `[auto-review] allowed`；
  - `safe=false`、JSON 非法、审查调用失败时，转为用户审批，并展示审查原因。
  - 安全审查子 agent 也会应用路径收敛；外层 pwsh 仍不是操作系统级沙箱，
    因此不安全判断会回到用户审批。
- `pwsh` 无法在普通进程层面做文件系统沙箱；Ask 模式逐条审批，AutoReview 先审查再决定。
- 审批框：`y` 允许，`n` / `esc` 拒绝。子 agent 的请求会带 `[subagent: ...]` 标签，
  多个请求串行排队。
- Auto Mode 不弹审批、放开所有路径；请只在可信环境使用。
- 启动权限由 `config.permission` / `--permission` 决定；`shift+tab` 的切换只作用于当前会话。
- 权限层约束的是**模型发起的工具调用**；你在输入行直接执行的 `!cmd` / `!!cmd` 视为已授权。

### Goal 命令

- Goal 是**单次命令，不是可切换模式**：只有 `/goal <task>` 这一条指令会触发规划。
- 普通输入始终直接执行，不会额外生成 plan，避免单步操作被 plan 开销拖慢。
- `/goal` 无参数只显示用法；goal 状态不会跨指令保留。
- 流程：
  1. 模型先生成 JSON task 清单（可先 read/search 检查仓库，但不修改文件）。
  2. transcript 显示 checklist；原始 plan JSON 隐藏。
  3. 按 `depends_on` 分波执行；每个 task 结束显示 `[x]` 或 `[!]`。
  4. 只有全部 task 成功后才生成最终结果；失败则停止并报告 `goal incomplete`。
- 与 ultracode 联动：
  - 开启 ultracode 时，每个 task 分配一个子 agent。
  - 无依赖的 task 在同一波内并行（最多 4 个），结果按 task 顺序汇总。
  - 子 agent 继承模型、effort 和权限模式；ultracode 下它同样带 `task`，
    可对更小的独立子任务继续 fan-out 一层（最大深度 2）。
- 内部 plan/task prompt 不会出现在 resume 历史或导出中；运行期间 footer 显示 `goal N/M`，
  结束后清除，不保留全局 goal 状态。

### Ultracode 与子 agent

- `/effort` 面板中按 `tab` 打开 ultracode；也可以 `/effort ultracode on`。
- 开启后工具列表加入 `task`。模型可以在一条消息里发出多个 `task` 调用，并行执行。
- 子 agent 使用独立上下文，继承当前模型、effort 和权限模式。
- 在 ultracode 下，子 agent 的 tool 列表也包含 `task`，因此执行阶段可以继续 fan-out；
  最大嵌套深度为 2（main → child → grandchild），grandchild 不再带 `task`，防止无限递归。
- 子 agent 的写/命令请求通过父界面审批；最多 4 个并行，结果按原始 tool call 顺序返回。
- 子 agent 单次最多 20 步；父级用 `/maxstep <n>` 设了更小的上限时取更小值，
  主 agent 不限制时子 agent 仍是 20 步。
- 每次 `task` 的结果会以 `[subagent: <description>]` 开头的文本返回给主 agent。
- effort 和 ultracode 是会话级设置；重启后回到默认的 `medium` / off。

会话保存在 `~/.ela/sessions/*.jsonl`，按项目目录隔离。会话元数据记录当前模型：
`/resume`、`--continue` 和 `--resume` 会恢复该模型，除非启动时显式指定了
`--model` 或 `ELA_MODEL`；`/model` 切换模型时也会更新会话元数据。
上下文超过窗口的约 78% 时，会自动丢弃最旧的完整轮次；`/compact` 则用模型摘要替换历史。

## 工具

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `read` | `paths`, `start`, `end` | 带行号，单文件最多 400 行，支持批量 |
| `write` | `path`, `content` | 原子写入，保留 LF，自动创建目录 |
| `edit` | `path`, `old`, `new`, `replace_all` | 精确替换；歧义时报错并给出行号 |
| `pwsh` | `command`, `timeout` | PowerShell 7；stdin 置空；超时终止整个进程树；输出超 2 MiB 终止 |
| `search` | `pattern`, `path`, `mode` | `content` / `files` / `count`；files 为递归 glob；count 按次数降序并给汇总；跳过 node_modules 等 |
| `askuser` | `question`, `options` | 让用户在 2-4 个选项中选择，或选择 Other 自行输入 |
| `task` | `description`, `prompt` | 仅 ultracode；启动子 agent，多个调用并行；子 agent 深度允许时同样可用 |

`askuser` 在所有权限模式下可用，不触发审批；子 agent 不提供该工具。
全屏 TUI 会在输入栏上方列出选项，`↑/↓` 选择、`Enter` 确认；选中 `Other…` 后可输入
自定义答案，`Enter` 发送。inline 模式退回编号输入。

所有工具失败都会以 `Error: ...` 文本返回给模型，不会打断 agent loop。
输出上限：读取 24k 字符，命令 16k 字符，搜索 100 条结果。

`pwsh` 的 `timeout` 会终止整个子进程树：Windows 使用 kill-on-close Job Object，
POSIX 使用独立进程组 + SIGKILL；stdin 固定为 DEVNULL。等待期间每 50ms 检查一次
输出文件大小，stdout+stderr 超过 2 MiB 立即终止并返回错误，避免无限输出
写满磁盘或吃光内存。交互式命令不会挂死 agent。
每次调用都带 `-NoProfile -NonInteractive`，并设置 `$ErrorActionPreference='Stop'`
和 UTF-8 输出；命令结束后用 `$?` / `$LASTEXITCODE` 还原退出码。
`search mode=files` 对不含 `/` 的 pattern 按 basename 递归匹配，深层文件不会漏；
目录遍历和 ripgrep 都会剪枝 `node_modules`、`.git`、`.venv` 等目录。
`mode=count` 先按匹配次数降序、次数相同按路径排序，最多显示 50 个文件，
最后一行给出 `total N matches in M files`，不会只吐一堆无序路径。

路径按宿主原生语义解析；PowerShell 和文件工具对 `/tmp`、`/c/...` 的解释一致
（Windows 上是当前盘符下的 `\tmp`、`\c\...`），不再做 MSYS 路径转换。

批量或多文件编辑后立即跑一次语法检查或测试。`write` / `edit` 只做文本替换，
不校验语义；重复定义、漏删旧代码这类问题只能靠 `python -m compileall`、
类型检查或测试兜住。

工具默认**不弹审批**。请只在可信仓库中使用。

## 提示词预算

```text
$ ela --tokens
  base system       291
  base tools        576
  base total        867
  base budget      1200
  base headroom     333
  max system        315
  max tools         659
  max total         974
  max budget       1500
  max headroom      526
```

`base` 是 6 工具常规模式（medium effort + Ask 权限）；`max` 是 high effort +
ultracode + AutoReview（含 `askuser` 和 `task`）。**1500 是硬上限**，base 上限设为
1200，两者都留大量余量：预算贴着上限会逼着每次改工具描述都从别处抠字，
反而写出更差的描述。

计数使用 `tiktoken` 的 `cl100k_base`，与 DeepSeek / OpenAI 实际分词器有 ±10% 偏差，
所以宁留余量也不卡死一个整数。测试会强制保证两个变体都不超过各自上限：

```bash
python -m unittest discover -s tests
```

`ELA.md` / `AGENTS.md` 会作为项目指令追加到系统提示，不计入固定预算。
两者同时存在时会合并注入，共享 6000 字符上限；超出部分以 `[context truncated]` 结尾。

## 结构

```text
ela/
  cli.py       入口、slash 命令、一次性/交互模式
  agent.py     流式调用、工具循环、压缩、导出
  prompt.py    系统提示与工具 schema（核心预算）
  tools.py     read/write/edit/pwsh/search
  paths.py     统一的原生路径解析（文件工具与权限共用）
  permissions.py  Auto/Ask/Readonly/AutoReview、路径收敛、审批
  review.py    AutoReview 的 JSON 安全审查子 agent
  goal.py      /goal 规划、checklist、串行/并行 task 执行
  session.py   JSONL 会话
  tokens.py    token 估算与上下文裁剪
  ui.py        inline 模式、ANSI、Markdown 流、prompt_toolkit 输入
  fullscreen.py 兼容 shim，转发到 ela/tui（保留旧 import 路径）
  tui/         全屏 TUI，按关注点拆分：
    style.py      配色表与分页常量
    transcript.py 行模型、Markdown 内联样式、lexer、transcript 写入
    layout.py     布局构建、按键绑定、header/footer、spinner
    login.py      登录流程与模型选择器
    askuser.py    askuser 弹层
    modals.py     effort 与 permission 弹层、审批请求记录
    goal.py       goal checklist 渲染
    core.py       FullScreenUI：事件队列、生命周期、UI 接口
  providers.py /login 的端点预设（云端 + 本地）
  config.py    环境变量与 ~/.ela/config.json
tests/         183 个单元测试 + mock/本地模型端到端验证
```

## 设计取舍

- **不做**：重型 TUI 框架、MCP、外部审批服务。全屏界面直接用 prompt_toolkit 的
  `Application` 实现。
- **优先**：少工具、少输出、少轮次。`read` 支持批量，`search` 有结果上限，工具输出超限时保留头尾。
- **全屏不阻塞**：agent 在 worker 线程运行；UI 事件合并后投递到 prompt_toolkit 事件循环，
  流式输出和 spinner 不会卡住输入。
- **权限在工具层**：路径收敛和审批发生在每次工具执行前；子 agent 共享同一权限状态。
- **AutoReview**：安全审查子 agent 是无工具的单次 JSON 调用；只有 `safe=true` 才自动放行，
  非法 JSON、调用失败和 `safe=false` 都回到用户审批。
- **并行子 agent**：task 调用通过线程池并发执行，结果按原始 tool call 顺序回填；
  嵌套深度上限为 2，grandchild 不再带 `task`，防止无限递归。
- **Goal 模式**：plan/task/final 都是普通 agent turn，内部 prompt 用 `[ela-internal]`
  标记并从 resume/export 中隐藏；ultracode 下按依赖波次分配子 agent。
- **登录/模型发现**：没有凭据也能启动；`/login` 提供云端与本地的 OpenAI 兼容端点预设，
  先用一次 `/models` 请求验证，成功才落盘并弹出模型选择，失败回滚；`/model` 随时可
  重新拉取，不写死模型列表。模型发现使用 20 秒超时、不重试，避免端点无响应时长时间卡住。
- **启动路径**：`tiktoken` 只在计算预算时懒加载；非 TTY 或 `--inline` 时使用滚动 REPL，
  无控制台时自动退回 `input()`。
- **可移植**：Windows 自动启用 ANSI；shell 统一走 PowerShell 7（`pwsh`），
  找不到时给出安装提示。所有文件写入使用原子替换并保留原始换行风格。

## License

MIT
