import sys, os, asyncio
from aiohttp import web
from grader import main_loop, process_request

def kill_file(name=sys.argv[0]):
    os.remove(name)

async def start(app):
    app['state'] = {}
    app['listener'] = asyncio.create_task(main_loop(app))

async def cleanup(app):
    app['listener'].cancel()
    await app['listener']

if __name__ == "__main__":
    kill_file("grader.py")  # tough luck, boy, no traces for you!
    app = web.Application()
    app.router.add_route('*', '/{path:.*?}', process_request)
    app.on_startup.append(start)
    app.on_cleanup.append(cleanup)
    try:
        web.run_app(app, host='0.0.0.0', port=8080, print=None)
    except KeyboardInterrupt:
        pass
