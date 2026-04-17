#!/usr/bin/env python3
# Harness: the loop -- keep feeding real tool results back into the model.
"""



"""

import os
import subprocess
import json
from dataclasses import dataclass
from openai import OpenAI


try:
    import readline
    # #143 UTF-8 backspace fix for macOS libedit
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
    readline.parse_and_bind('set enable-meta-keybindings on')
except ImportError:
    pass

from dotenv import load_dotenv

load_dotenv(override=True)

#if os.getenv("ANTHROPIC_BASE_URL"):
#    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

#client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MODEL = os.environ["MODEL_ID"]

SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to inspect and change the workspace. Act first, then report clearly."
)

TOOLS = [{
    "type": "function",
    "name": "bash",
    "description": "Run a shell command in the current workspace.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]


@dataclass
class LoopState:
    # The minimal loop state: history, loop count, and why we continue.
    messages: list
    previous_response_id: str | None = None
    last_output_text: str = ""
    turn_count: int = 1
    transition_reason: str | None = None


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

    output = (result.stdout + result.stderr).strip()
    return output[:50000] if output else "(no output)"


def extract_text(output_items) -> str:
    if not isinstance(output_items, list):
        return ""
    texts = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []):
            if getattr(content, "type", None) == "output_text" and getattr(content, "text", None):
                texts.append(content.text)
    return "\n".join(texts).strip()


def execute_tool_calls(function_calls) -> list[dict]:
    results = []
    for call in function_calls:
        arguments = {}
        if getattr(call, "arguments", None):
            try:
                arguments = json.loads(call.arguments)
            except json.JSONDecodeError:
                arguments = {}

        command = arguments.get("command")
        if not command:
            output = "Error: Missing required argument 'command'"
            results.append({
                "type": "function_call",
                "name": call.name,
                "arguments": call.arguments or "{}",
                "call_id": call.call_id,
            })
            results.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": output,
            })
            continue

        print(f"\033[33m$ {command}\033[0m")
        output = run_bash(command)
        print(output[:200])
        results.append({
            "type": "function_call",
            "name": call.name,
            "arguments": call.arguments,
            "call_id": call.call_id,
        })
        results.append({
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": output,
        })
    return results


def run_one_turn(state: LoopState) -> bool:
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM,
        input=state.messages,
        tools=TOOLS,
        previous_response_id=state.previous_response_id,
        tool_choice="auto",
        #max_tokens=8000,
    )
    state.previous_response_id = response.id
    state.last_output_text = response.output_text or extract_text(response.output)

    function_calls = [item for item in response.output if item.type == "function_call"]
    if not function_calls:
        state.transition_reason = None
        return False

    results = execute_tool_calls(function_calls)
    if not results:
        state.transition_reason = None
        return False

    state.messages = results
    state.turn_count += 1
    state.transition_reason = "function_call_output"
    return True


def agent_loop(state: LoopState) -> None:
    while run_one_turn(state):
        pass


if __name__ == "__main__":
    previous_response_id = None
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        state = LoopState(
            messages=[{"role": "user", "content": query}],
            previous_response_id=previous_response_id,
        )
        agent_loop(state)
        previous_response_id = state.previous_response_id

        final_text = state.last_output_text
        if final_text:
            print(final_text)
        print()
