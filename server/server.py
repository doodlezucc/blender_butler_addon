import aiohttp
from aiohttp import web
import asyncio
import json
import socket

from aiohttp.web_request import Request

sockets = []
tasks = {}

def update(id, title=None, description=None, progress=None):
    if not id in tasks:
        tasks[id] = {
            "title": "",
            "description": "",
            "progress": 0,
            "icon": ""
        }
    
    task = tasks[id]
    if title: task["title"] = title
    if description: task["description"] = description
    if progress: task["progress"] = float(progress)

    # print(f"Updated task {id}")


async def send_tasks():
    for socket in sockets:
        await socket.send_json(tasks)

def register(websocket):
    sockets.append(websocket)
    print("Registered")

def unregister(websocket):
    sockets.remove(websocket)
    print("Unregistered")

async def json_update(data):
    obj = json.loads(data)
    for id in obj:
        title = obj[id].get("title")
        desc = obj[id].get("description")
        progress = obj[id].get("progress")
        update(id, title=title, description=desc, progress=progress)
    await send_tasks()

async def http_handler(request):
    return web.Response(text="Hello, world")

async def info_handler(request):
    content = {
        "name": socket.gethostname()
    }
    return web.Response(text=json.dumps(content))

async def update_handler(request: Request):
    id = request.match_info["id"]

    def esc(key):
        value = request.query.getone(key, None)
        return value
    
    update(id, esc("title"), esc("description"), esc("progress"))
    await send_tasks()
    return web.Response(text="OK")


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    register(ws)
    await ws.send_json(tasks)

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == "close":
                await ws.close()
            else:
                json_update(msg.data)
        elif msg.type == aiohttp.WSMsgType.ERROR:
            print("ws connection closed with exception %s" % ws.exception())

    unregister(ws)
    return ws


def create_runner():
    app = web.Application()
    app.add_routes([
        web.get("/",   http_handler),
        web.get("/info", info_handler),
        web.get("/update/{id}", update_handler),
        web.get("/ws", websocket_handler),
    ])
    return web.AppRunner(app)


async def start_server(port=2048):
    runner = create_runner()
    await runner.setup()
    site = web.TCPSite(runner, port=port)
    await site.start()
    print(f"Server listening on port {port}")


def start():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server())
    loop.run_forever()


if __name__ == "__main__":
    start()