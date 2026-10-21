"""Claude Code 的权限确认工具（--permission-prompt-tool）。

Claude 遇到不在自动放行名单里的操作时会调用这里的 approve，
本脚本把请求转给中心服务，等网页端或语音里有人同意/拒绝后再返回。
由中心服务为每个任务生成的 --mcp-config 启动，通过环境变量拿到会话信息。
"""
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

CENTER_URL = os.environ["CENTER_URL"]
CENTER_TOKEN = os.environ["CENTER_TOKEN"]
CENTER_SESSION = os.environ["CENTER_SESSION"]

mcp = FastMCP("center")


@mcp.tool()
async def approve(tool_name: str, input: dict, tool_use_id: str | None = None) -> str:
    """请求用户确认一个操作。"""
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            r = await client.post(
                CENTER_URL + "/api/approvals",
                headers={"Authorization": f"Bearer {CENTER_TOKEN}"},
                json={"session_id": CENTER_SESSION, "tool_name": tool_name, "input": input},
            )
        decision = r.json() if r.status_code == 200 else {"allow": False, "message": "中心服务拒绝了请求"}
    except httpx.HTTPError:
        decision = {"allow": False, "message": "连不上中心服务，无法确认"}
    if decision.get("allow"):
        return json.dumps({"behavior": "allow", "updatedInput": input}, ensure_ascii=False)
    return json.dumps(
        {"behavior": "deny", "message": decision.get("message") or "用户拒绝了这个操作，不要重试同样的操作"},
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
