"""小智 AI 的 MCP 工具：把语音指令转交给台式机上的中心服务（server.py）。

由 mcp_pipe.py 启动，通过 stdio 与小智 MCP 接入点通信。
注意：stdout 是协议通道，日志只能写到 stderr。
"""
import logging
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s [claude_mcp] %(message)s")
log = logging.getLogger("claude_mcp")

CENTER_URL = os.environ.get("CENTER_URL", f"http://127.0.0.1:{os.environ.get('CENTER_PORT', '8080')}")
CENTER_TOKEN = os.environ.get("CENTER_TOKEN", "")
# 同步等待这么久，超时就转为后台任务，避免小智那边等太久
WAIT_SECONDS = float(os.environ.get("CLAUDE_WAIT_SECONDS", "40"))
MAX_REPLY_CHARS = 500

mcp = FastMCP("claude-desktop")


async def _call(method: str, path: str, **kwargs) -> str:
    try:
        async with httpx.AsyncClient(timeout=WAIT_SECONDS + 15) as client:
            r = await client.request(method, CENTER_URL + path,
                                     headers={"Authorization": f"Bearer {CENTER_TOKEN}"}, **kwargs)
    except httpx.HTTPError as e:
        log.warning("连不上中心服务：%r", e)
        return "连不上台式机上的中心服务，请确认 server.py 已经启动。"
    if r.status_code != 200:
        return f"中心服务返回错误：{r.json().get('detail', r.status_code)}"
    reply = r.json()["reply"]
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "……（完整内容请在电脑上查看）"
    return reply


@mcp.tool()
async def claude_task(task: str, allow_edit: bool = False, continue_last: bool = False) -> str:
    """让台式电脑上的 Claude 助手完成一项工作。D 盘是用户的文件库。
    用户要求查找、阅读、总结、整理电脑上的文件，写文档，或其他需要在电脑上完成的事情时调用。

    参数：
    - task：尽量保留用户的原话，不要概括或省略，特别是文件名、文件夹名和具体要求。
    - allow_edit：为 true 时允许新建或修改文件。只有用户明确要求修改或保存文件，
      并且你已经向用户复述要改什么、用户口头确认之后，才能设为 true。
    - continue_last：用户在追问或接着上一个任务继续时设为 true，Claude 会记得上次的内容。

    如果返回“已开始”，说明任务耗时较长，告诉用户稍后可以问“任务进度”。
    把返回结果简短地转述给用户。
    """
    log.info("语音任务（%s）：%s", "可修改" if allow_edit else "只读", task)
    return await _call("POST", "/api/voice", json={
        "task": task, "allow_edit": allow_edit, "continue_last": continue_last, "wait": WAIT_SECONDS,
    })


@mcp.tool()
async def claude_task_status() -> str:
    """查询台式电脑上 Claude 最近一个语音任务的进度和结果。
    用户问“任务进度”“好了没有”“结果是什么”时调用。
    """
    return await _call("GET", "/api/voice/status")


if __name__ == "__main__":
    log.info("中心服务地址 %s", CENTER_URL)
    mcp.run(transport="stdio")
