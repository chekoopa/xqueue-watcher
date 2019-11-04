import sys
import asyncio
from aiohttp import web
import helper


async def process_request(request):
    try:
        path = request.path.lower()
        state = request.app['state']

        if state['current_run'] is not None:
            return web.Response(status=500)

        if path in ("/my_spoon_is_too_big", ):
            state['current_run'] = True
        else:
            state['current_run'] = False
            return web.Response(status=404)

        return web.Response(status=200, text="I am a banana!")
    except Exception as e:
        print(e, file=sys.stderr)
        return web.Response(status=400, text="Bad boy!")


async def main_loop(app):
    state = app['state']
    state['buffer'] = []
    state['client'] = None

    state['current_run'] = None
    rates = []
    msg = ["Results:"]
    try:
        while True:
            outs = helper.update_client(state, timeout=1)
            if outs is not None:
                if outs.timeout:
                    msg.append("- Timeout")
                    state['current_run'] = None
                    rates.append(False)
                elif outs.code != 0:
                    msg.append("- Runtime error\n")
                    state['current_run'] = None
                    rates.append(False)
                elif state['current_run'] is None:
                    msg.append("- No correct requests")
                    rates.append(False)
                else:
                    msg.append("- Normal run (either good or bad)")
                    rates.append(state['current_run'])
                    state['current_run'] = None
            await asyncio.sleep(0.005)
            if len(rates) >= 20:
                score = 1 if all(rates) else 0
                helper.exit_grader(score, "\n".join(msg))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        helper.bail_exception()
