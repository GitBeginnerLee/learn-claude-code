#!/usr/bin/env python3
# Harness: planning -- keep the current session plan outside the model's head.
"""
s03_todo_write.py - Session Planning with TodoWrite

This chapter is about a lightweight session plan, not a durable task graph.
The model can rewrite its current plan, keep one active step in focus, and get
nudged if it stops refreshing the plan for too many rounds.



1、导入环境和包

2、准备调用API参数
	client
	model
	system
	tools：schema

3、创建两个类对象--方便后续直接使用类对象数据结构
	PlanItem
	PlanningState

4、设置PLAN_REMINDER_INTERVAL计划项最大执行轮次

5、创建todomanager类对象
	update方法：将API response的items整理成特定数据格式
	render方法：将update()之后的ltems处理成一串Str，记录到messages中
	note_round_without_update方法：记录没有调用update()的次数，即items中单个计划项的执行轮次
	reminder方法：监控rounds_since_update超过PLAN_REMINDER_INTERVAL时向messages中插入提醒：更新items计划



6、工具调用入口：TOOL_HANDLERS

7、创建当前workplace路径：workdir
8、tools指令前置安全沙箱：safe_path

9、tools内指令函数：
	run_bash
	run_read
	run_write
	run_edit
	todomanager

10、设置extract_text确保client端最终输出文本

11、设置agent loop
	发送请求响应
	接受响应response，判断结束或者tool_use调用
	TOOL_HANDLERS中寻找tool
	output接收tool执行结果
		增加了一个todo的监控判断，即记录tool_use的执行轮次
		追加messages提醒更新items计划项
	result追加到messages

12、设置初始化流程__name__


"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
PLAN_REMINDER_INTERVAL = 3 #添加了每条计划的最大等待回合数

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use the todo tool for multi-step work.
Keep exactly one step in_progress when a task has multiple steps.
Refresh the plan as work advances. Prefer tools over prose."""


@dataclass
class PlanItem: #计划条目类，用于存储计划条目的内容、状态和活动形式。
    content: str
    status: str = "pending"
    active_form: str = ""


@dataclass
class PlanningState: #计划状态类，用于存储计划条目列表和回合数。
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0


class TodoManager:
    def __init__(self):
        self.state = PlanningState()

    def update(self, items: list) -> str: #这个函数的作用是整理AI返回的items，然后交给render输出成一串字符串加入到messages
        if len(items) > 12: #如果计划条目列表长度大于12，则抛出错误。
            raise ValueError("Keep the session plan short (max 12 items)") #示例：Keep the session plan short (max 12 items)

        normalized = []
        in_progress_count = 0
        for index, raw_item in enumerate(items): #enumerate(可迭代对象) 会返回一个迭代器，遍历原序列时逐项产生：(0, 第0个元素), (1, 第1个元素), (2, 第2个元素), ...
            content = str(raw_item.get("content", "")).strip()
            status = str(raw_item.get("status", "pending")).lower()
            active_form = str(raw_item.get("activeForm", "")).strip()

            if not content: #如果计划条目的内容为空，则抛出错误。
                raise ValueError(f"Item {index}: content required")
            if status not in {"pending", "in_progress", "completed"}: #如果计划条目的状态不在pending、in_progress或completed，则抛出错误。
                raise ValueError(f"Item {index}: invalid status '{status}'")
            if status == "in_progress": #如果计划条目的状态为in_progress，则计数器加1。
                in_progress_count += 1

            normalized.append(PlanItem(
                content=content,
                status=status,
                active_form=active_form,
            ))

        if in_progress_count > 1: #设置的是status为in_progress的计划条目最多只能有1条。
            raise ValueError("Only one plan item can be in_progress")

        self.state.items = normalized
        self.state.rounds_since_update = 0
        return self.render()

    def note_round_without_update(self) -> None:
        self.state.rounds_since_update += 1

    def reminder(self) -> str | None: #这个函数的用处是监控单个计划项的执行轮次然后提醒AI更新计划项
        if not self.state.items: #如果计划条目列表为空，则返回None。
            return None
        if self.state.rounds_since_update < PLAN_REMINDER_INTERVAL: #如果回合数小于每条计划的最大等待回合数，则返回None。
            return None
        return "<reminder>Refresh your current plan before continuing.</reminder>" #如果回合数大于每条计划的最大等待回合数，则返回提醒信息。

    def render(self) -> str: #这个函数的用处是把items处理成一串文本添加到messages中方便AI阅读
        if not self.state.items: #如果计划条目列表为空，则返回"No session plan yet."。
            return "No session plan yet."

        lines = []
        for item in self.state.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                    }[item.status] #这里其实是根据{}字典取item.status对应的值。
            line = f"{marker} {item.content}" #示例：[>] 阅读 s03_todo_write.py 中的 TodoManager
            if item.status == "in_progress" and item.active_form: #如果计划条目的状态为in_progress且active_form不为空，追加active_form。
                line += f" ({item.active_form})" #示例：[>] 阅读 s03_todo_write.py 中的 TodoManager (Reading the failing test)
            lines.append(line)

        completed = sum(1 for item in self.state.items if item.status == "completed") #计算已完成计划条目的数量。
        lines.append(f"\n({completed}/{len(self.state.items)} completed)") #示例：\n(1/3 completed)
        return "\n".join(lines)


TODO = TodoManager()


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

    output = (result.stdout + result.stderr).strip()
    return output[:50000] if output else "(no output)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo": lambda **kw: TODO.update(kw["items"]), #工具分发器中添加了todo工具，用于更新计划。
}

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in a file once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    { #添加了todo工具，用于更新计划。
        "name": "todo",
        "description": "Rewrite the current session plan for multi-step work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Optional present-continuous label.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
]


def extract_text(content) -> str: #这个函数的用处是让最后client端只输出text文本
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None) #尽量读 block.text，没有就当 None
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def agent_loop(messages: list) -> None:
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use": #如果响应的停止原因不是tool_use，则返回。说明模型没有调用工具，任务完成。
            return

        results = []
        used_todo = False
        for block in response.content:
            if block.type != "tool_use": #如果块的类型不是tool_use，则跳过。
                continue

            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}" 
                #调用todu_update()-->render()输出一串文本：'[ ] 任务A\n[>] 任务B\n(1/2 completed)'
            except Exception as exc:
                output = f"Error: {exc}"

            print(f"> {block.name}: {str(output)[:200]}") #打印todo调用，拼接计划执行情况。 #打印tool_use调用结果
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            })
            if block.name == "todo":
                used_todo = True

        if used_todo: 
            #这里的逻辑涉及到AI的返回信息，在in_progress只有一个的情况下，AI在完成一条计划项之前不会再次调用todo工具
            #就是说只要没有调用todo工具就说明AI的tool_use卡在了某一个计划项，所以要记录执行轮次并监督，超过3次之后强制提醒AI更新计划，即调用todo工具
            TODO.state.rounds_since_update = 0
        else:
            TODO.note_round_without_update() #记录当前计划执行轮次
            reminder = TODO.reminder() #如果rounds_since_update大于3，插入提醒AI更新计划
            if reminder:
                results.insert(0, {"type": "text", "text": reminder})

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        agent_loop(history)

        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
