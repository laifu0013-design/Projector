"""把本地 stdio MCP 服务接到小智的 MCP 接入点（WebSocket），断线自动重连。

用法：先设置环境变量 MCP_ENDPOINT，然后运行
    python mcp_pipe.py claude_mcp.py
"""
import asyncio
import logging
import os
import sys

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [mcp_pipe] %(message)s")
log = logging.getLogger("mcp_pipe")

MAX_BACKOFF = 60


async def run_once(endpoint: str, script: str) -> None:
    async with websockets.connect(endpoint, max_size=4 * 1024 * 1024) as ws:
        log.info("已连接小智 MCP 接入点")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            limit=4 * 1024 * 1024,
        )

        async def ws_to_proc():
            async for msg in ws:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8")
                proc.stdin.write((msg.rstrip("\n") + "\n").encode("utf-8"))
                await proc.stdin.drain()

        async def proc_to_ws():
            while line := await proc.stdout.readline():
                await ws.send(line.decode("utf-8").rstrip("\n"))

        tasks = [asyncio.create_task(ws_to_proc()), asyncio.create_task(proc_to_ws())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()


async def main() -> None:
    endpoint = os.environ.get("MCP_ENDPOINT")
    if not endpoint or len(sys.argv) < 2:
        log.error("请先设置 MCP_ENDPOINT，并指定脚本：python mcp_pipe.py claude_mcp.py")
        sys.exit(1)
    backoff = 1
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await run_once(endpoint, sys.argv[1])
            log.warning("连接已关闭")
        except Exception as e:
            log.warning("连接出错：%r", e)
        # 稳定连了一阵后断开，就从短间隔重新开始重连
        if asyncio.get_running_loop().time() - started > 60:
            backoff = 1
        log.info("%d 秒后重连", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
