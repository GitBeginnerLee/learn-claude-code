#!/usr/bin/env python3
# Harness: tool dispatch -- expanding what the model can reach.
"""
s02_tool_use.py - Tool dispatch + message normalization

The agent loop from s01 didn't change. We added tools to the dispatch map,
and a normalize_messages() function that cleans up the message list before
each API call.

Key insight: "The loop didn't change at all. I just added tools."

一次test

整体思路流程：
导入环境和包

1、准备调用API参数
    client
    model
    system
    tools：schema

2、工具调用入口：TOOL_HANDLERS

3、创建当前workplace路径：workdir
4、tools指令前置安全沙箱：safe_path

5、tools内指令函数：
    run_bash
    run_read
    run_write
    run_edit

6、messages合规处理：

    由于API协议硬性约束：
        只接受协议定义的字段 (内部元数据会导致 400 错误)
        每个 tool_use 块必须有匹配的 tool_result (通过 tool_use_id 关联)
        user / assistant 消息必须严格交替 (不能连续两条同角色)


1）messages结构规范化
2）tool_use配对，缺失tool_result则补充占位符
    收集tool_use_id
    将messages中tool_use block中tool_use_id匹配，缺失的补充占位符
3）将messages中连续role相同的做合并

7、设置agent loop

8、设置初始化流程__main__

"""

import os
import subprocess
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"): #这里是判断环境变量中是否存在ANTHROPIC_BASE_URL，如果存在则删除环境变量中的ANTHROPIC_AUTH_TOKEN。
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None) 

WORKDIR = Path.cwd() #这里是获取当前工作目录，是一个Path对象。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL")) #这里是创建一个Anthropic对象，base_url是Anthropic的API地址。
MODEL = os.environ["MODEL_ID"] #这里是获取环境变量中的MODEL_ID，是一个字符串。

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve() #这里是将WORKDIR和p拼接成一个Path对象，并调用resolve方法解析成绝对路径。
    if not path.is_relative_to(WORKDIR): #is_relative_to方法判断path是否是WORKDIR的子路径。
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text() 
        lines = text.splitlines() #这里是将text按行分割成一个列表。
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"] #这里是将lines列表中前limit个元素和后len(lines) - limit个元素拼接成一个列表。
        return "\n".join(lines)[:50000] #这里是将lines列表中的元素拼接成一个字符串，并返回前50000个字符。这里设置50000个字符是为了防止文本过长，导致API调用失败。
    except Exception as e: #这里是捕获safe_path(path).read_text()异常，并返回异常信息。只捕获try块中的异常，as将异常写入e变量。
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True) #这里是创建一个目录，parents=True表示创建父目录，exist_ok=True表示如果目录存在则不创建。
        fp.write_text(content) #这里是将content写入fp文件中。
        return f"Wrote {len(content)} bytes to {path}" #这里是返回写入的字节数。
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text() #这里是将fp文件中的内容读取出来，并赋值给content变量。
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}" 
    except Exception as e:
        return f"Error: {e}"


# -- Concurrency safety classification --
# Read-only tools can safely run in parallel; mutating tools must be serialized.
CONCURRENCY_SAFE = {"read_file"}
CONCURRENCY_UNSAFE = {"write_file", "edit_file"}

# -- The dispatch map: {tool_name: handler} --
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
]


def normalize_messages(messages: list) -> list:
    """Clean up messages before sending to the API.

    Three jobs:
    1. Strip internal metadata fields the API doesn't understand
    2. Ensure every tool_use has a matching tool_result (insert placeholder if missing)
    3. Merge consecutive same-role messages (API requires strict alternation)
    """
    cleaned = []
    for msg in messages:
        clean = {"role": msg["role"]}
        if isinstance(msg.get("content"), str):
            clean["content"] = msg["content"]
        elif isinstance(msg.get("content"), list):
            clean["content"] = [ #使用了列表推导式
                                {k: v for k, v in block.items() #使用了字典推导式
                                if not k.startswith("_")}
                                for block in msg["content"]
                                if isinstance(block, dict)
                            ]
        else:
            clean["content"] = msg.get("content", "")
        cleaned.append(clean)

    # Collect existing tool_result IDs
    existing_results = set()
    for msg in cleaned:
        if isinstance(msg.get("content"), list): #这里使用msg.get("content")这种写法的原因是若key不存在不会报错，而是返回none，msg.get("content")等价于msg["content"]，但是msg["content"]若不存在会报错。
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    existing_results.add(block.get("tool_use_id"))

    # Find orphaned tool_use blocks and insert placeholder results
    for msg in cleaned:
        if msg["role"] != "assistant" or not isinstance(msg.get("content"), list): #过滤掉不是助手消息和不是列表的消息。
            continue
        for block in msg["content"]:
            if not isinstance(block, dict): #过滤掉不是字典的消息。
                continue
            if block.get("type") == "tool_use" and block.get("id") not in existing_results: #过滤掉不是工具使用消息和不是工具使用消息ID的消息。
                cleaned.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": block["id"],
                     "content": "(cancelled)"}
                ]})

    # Merge consecutive same-role messages
    if not cleaned:
        return cleaned
    merged = [cleaned[0]] #这里将cleaned列表中的第一个元素赋值给merged列表。就是说这是客户端第一次输入消息，不需要做数据合并。
    for msg in cleaned[1:]: #cleaned[1:]的元素就是调用API返回的消息，需要做数据合并。
        if msg["role"] == merged[-1]["role"]:
            prev = merged[-1]
            prev_c = prev["content"] if isinstance(prev["content"], list) \
                else [{"type": "text", "text": str(prev["content"])}]
            curr_c = msg["content"] if isinstance(msg["content"], list) \
                else [{"type": "text", "text": str(msg["content"])}]
            prev["content"] = prev_c + curr_c
        else:
            merged.append(msg)
    return merged


def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=normalize_messages(messages),
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name) #这里是查找TOOL_HANDLERS字典中对应的值返回给handler，就是一个简单的赋值过程
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}" #这里用了三元表达式，如果handler不为空，则执行handler(**block.input)，否则执行f"Unknown tool: {block.name}"。
                #handler(**block.input) 是执行handler函数，并传递block.input作为参数，block.input是工具的输入参数，是一个字典。
                #handler(**block.input)中**是解包操作，将block.input字典中的键值对解包成单独的参数传递给handler函数。
                #这里handler=lambda **kw: run_bash(kw["command"]，kw["command"]是block.input字典中的键，值是block.input字典中的值。
                print(f"> {block.name}:")
                print(output[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
