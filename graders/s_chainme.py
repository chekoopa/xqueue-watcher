import asyncio
import random
from aiohttp import web
import helper


STATIONS = 3


async def process_request(request):
    try:
        path = request.path.lower()
        state = request.app['state']

        if path in ("/start", ):
            clue = random.randint(0, 1000)
            state['current_run'] = [0, clue]
            return web.Response(status=200, text=str(clue))
        elif path[1:].isdecimal():
            (steps, clue) = state['current_run']
            text = path[1:]
            if text.isdecimal() and int(text) == clue:
                if steps == STATIONS - 1:
                    state['current_run'] = [steps + 1, None]
                    return web.Response(status=202, text="It works!")
                clue = random.randint(0, 1000)
                state['current_run'] = [steps+1, clue]
                return web.Response(status=200, text=str(clue))

        state['current_run'][1] = None
        return web.Response(status=500, text="MORTIS")

    except Exception as e:
        helper.eprint(e)
        return web.Response(status=400, text="Yikes!")


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
                    points = (
                        0 if state['current_run'] is None else
                        state['current_run'][0]
                    )
                    rates.append(points / STATIONS)
                    msg.append("- Timeout (partial run possible)")
                    state['current_run'] = None
                elif outs.code != 0:
                    helper.eprint(outs, state['current_run'])
                    points = (
                        0 if state['current_run'] is None else
                        state['current_run'][0]
                    )
                    rates.append(points/STATIONS)
                    msg.append(f"- Runtime error (partial run possible)\n")
                    state['current_run'] = None
                elif state['current_run'] is None:
                    msg.append("- Session wasn't started")
                    rates.append(0)
                else:
                    msg.append("- Normal run (either good or bad)")
                    rates.append(state['current_run'][0]/STATIONS)
                    state['current_run'] = None
            elif state['current_run']:
                if state['current_run'][1] is None:
                    helper.eprint("early_bird", state['current_run'])
                    msg.append("- Normal run (either good or bad)")
                    rates.append(state['current_run'][0] / STATIONS)
                    helper.kill_client(state)
                    state['current_run'] = None
            await asyncio.sleep(0.005)
            if len(rates) >= 20:
                score = helper.mean(rates)
                helper.exit_grader(score, "\n".join(msg))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        helper.bail_exception()
