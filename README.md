# 私人定制 AI 助手（Personal AI Assistant）

一个基于个人使用习惯定制的桌面 AI 助手，灵感来自 OpenClaw，简化后聚焦"偏好记忆"。

- **云端大模型**：连接 DeepSeek API（deepseek-chat），对话与偏好数据保存本机
- **偏好记忆**：自动学习你的称呼、职业、代码风格、回复格式偏好，每次对话自动注入
- **四大能力**：对话 + 记忆、文件读写、代码执行（带安全沙箱）、联网搜索
- **桌面应用**：PySide6 原生窗口，三栏布局，流式输出不卡顿

---

## 一、前置环境准备

### 1. 申请 DeepSeek API Key

在 https://platform.deepseek.com 注册并申请 API Key，然后在应用的 **设置** 中填入。

- 模型默认使用 `deepseek-chat`（支持工具调用）
- 注意：`deepseek-reasoner`（R1）不支持工具调用，接本助手请使用 `deepseek-chat`

### 2. 创建 Python 3.11 环境

你当前的 conda base 可能是 Python 3.9，不满足依赖要求（ddgs 需要 ≥3.10），需新建：

```powershell
conda create -n ai_assistant python=3.11 -y
conda activate ai_assistant
```

### 3. 安装依赖

```powershell
cd c:\aa\personal_ai_assistant
pip install -r requirements.txt
```

---

## 二、启动应用

### 方式一：使用启动脚本（推荐）

双击 `run.bat`，它会自动激活 conda 环境并启动 GUI：

```powershell
.\run.bat
```

> 如果你的 conda 环境名不是 `ai_assistant`，请编辑 `run.bat` 修改 `ENV_NAME`。

### 方式二：手动启动 GUI

```powershell
conda activate ai_assistant
cd c:\aa\personal_ai_assistant
python main.py
```

### 方式三：CLI 模式（开发调试用，无需 GUI）

```powershell
conda activate ai_assistant
cd c:\aa\personal_ai_assistant
python cli_chat.py
```

CLI 模式适合快速验证 LLM 连通性和 Agent 循环，不用等 GUI 重启。

---

## 三、首次使用

1. 启动后点 **设置**，填入 DeepSeek API Key（base_url 默认 `https://api.deepseek.com/v1`，模型默认 `deepseek-chat`）。
2. 状态栏显示绿色"已连接"即后端可用；若为红色，检查 API Key 与网络。
3. 在输入框输入消息，Ctrl+Enter 发送。
4. 试着告诉助手你的偏好，例如：
   - "我叫小张，我是做前端开发的"
   - "我主要用 TypeScript 写代码"
   - "回复尽量简洁，代码块带语言标注"
5. 助手会自动记住这些偏好。打开右侧 **记忆面板** 可查看/编辑已记录的偏好。
6. 下次对话时，助手会自动应用这些偏好。

### 能力演示

- **多步任务**："搜一下 PySide6 最新版本，把结果写到 notes.txt"
- **代码执行**："写个 Python 脚本计算斐波那契数列前 20 项并运行"
- **文件读取**："读一下 config.json 的内容"（支持 Word/PPT/Excel）
- **偏好回想**："我叫什么名字？"（验证记忆注入生效）

---

## 四、安全说明

### 代码执行沙箱

本项目采用**防御性限制**而非真隔离：

- **相对路径禁锢**：相对路径文件操作限制在沙箱目录内，拒绝 `..` 越界；绝对路径可自由访问
- **危险命令黑名单**：拦截 `rm/del/format/diskpart/shutdown/reg add/taskkill` 等
- **超时控制**：子进程默认 30 秒超时自动终止
- **Shell 命令确认**：执行 shell 命令前会弹窗让你确认

> **诚实声明**：这不是真正的容器隔离。如果需要更强隔离，建议用 Docker（见下方"可选加固"）。

### 可选加固（Docker 沙箱）

安装 Docker Desktop 后，可修改 `tools/code_exec.py` 把 `ExecCommandTool` 切换为在容器内执行。本项目 v1 未集成此选项，留作扩展。

---

## 五、项目结构

```
personal_ai_assistant/
├── main.py                  # GUI 入口
├── cli_chat.py              # CLI 入口（调试用）
├── config.py                # 配置管理（DeepSeek API 配置）
├── requirements.txt
├── run.bat                  # Windows 启动脚本
├── core/
│   ├── agent.py             # Agent 循环（tool calling + reasoning 回传）
│   ├── llm_client.py        # DeepSeek API 封装，流式 tool_calls 累积
│   ├── memory.py            # 偏好记忆（规则+LLM 提取，注入 system prompt）
│   └── conversation.py      # 对话历史 + 滑动窗口裁剪
├── tools/
│   ├── base.py              # Tool 抽象基类 + ToolRegistry
│   ├── file_ops.py          # 文件读写（支持 Word/PPT/Excel）
│   ├── code_exec.py         # 代码/命令执行（沙箱+确认）
│   └── web_search.py        # ddgs 联网搜索
├── gui/
│   ├── main_window.py       # 主窗口（三栏布局）
│   ├── chat_widget.py       # 聊天区（Markdown 渲染）
│   ├── settings_dialog.py   # 设置对话框
│   ├── memory_panel.py      # 偏好记忆面板
│   └── workers.py           # QThread AgentWorker
└── data/                    # 运行时生成
    ├── assistant.db         # SQLite（对话历史+偏好）
    ├── config.json          # 用户配置（含 API Key，勿上传公开仓库）
    └── sandbox/             # 代码执行工作区
```

---

## 六、故障排查

### Q: 状态栏显示未连接
- 检查 API Key 是否填写正确
- 检查网络能否访问 https://api.deepseek.com
- 确认模型不是 `deepseek-reasoner`（R1 不支持工具调用）

### Q: 模型响应很慢
- 云端 API 受网络与负载影响，属正常
- 检查本地网络 / 代理设置

### Q: 工具调用失败
- 查看 GUI 中工具调用过程的错误提示
- 联网搜索失败可能是 DuckDuckGo 限流，稍后重试
- 文件操作失败检查路径是否正确

### Q: 偏好没有被记住
- 确认说的偏好能被规则匹配（如"我叫X"格式）
- 打开记忆面板查看是否有记录
- LLM 提取每 5 轮触发一次，可能需要多聊几轮