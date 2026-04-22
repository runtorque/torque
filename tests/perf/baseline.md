# Loom perf profile harness report

- Mode: `baseline`
- Generated: `2026-04-22T22:32:42.082076+00:00`
- Repo: `loom/courier/perf-harness-n10-n20-n30-ac43a0c` @ `74470daae4a7`
- Matrix: `[10, 20, 30]`
- Duration: `1.0` sec/run

## Acceptance evidence

| N | sent | accepted | loss | snapshot max bytes | WS connect p95 ms | loop lag p95 ms | SQLite p95 ms | WS payload p95 bytes | WS broadcast p95 ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 26 | 26 | 0 | 19511 | 0.85 | 2.40 | 0.29 | 31498 | 6.81 |
| 20 | 51 | 51 | 0 | 37091 | 1.33 | 2.44 | 0.23 | 60212 | 13.37 |
| 30 | 83 | 83 | 0 | 54671 | 1.83 | 2.42 | 0.24 | 156158 | 0.51 |

## CPU profile top functions

### N=10

| rank | cumulative sec | total sec | calls | function |
|---:|---:|---:|---:|---|
| 1 | 2.7787 | 0.0013 | 148 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/base_events.py:1966(_run_once)` |
| 2 | 2.7187 | 0.0004 | 148 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/selectors.py:540(select)` |
| 3 | 2.7183 | 2.7183 | 159 | `~:0(<method 'control' of 'select.kqueue' objects>)` |
| 4 | 0.0579 | 0.0002 | 233 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/events.py:92(_run)` |
| 5 | 0.0576 | 0.0003 | 233 | `~:0(<method 'run' of '_contextvars.Context' objects>)` |
| 6 | 0.0275 | 0.0005 | 39 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:550(start)` |
| 7 | 0.0238 | 0.0003 | 379 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/traceback.py:437(extract)` |
| 8 | 0.0235 | 0.0063 | 379 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/traceback.py:459(_extract_from_extended_frame_gen)` |
| 9 | 0.0216 | 0.0002 | 29 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:507(_handle_request)` |
| 10 | 0.0197 | 0.0003 | 29 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_app.py:527(_handle)` |

### N=20

| rank | cumulative sec | total sec | calls | function |
|---:|---:|---:|---:|---|
| 1 | 2.7799 | 0.0015 | 154 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/base_events.py:1966(_run_once)` |
| 2 | 2.6931 | 0.0004 | 154 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/selectors.py:540(select)` |
| 3 | 2.6927 | 2.6927 | 175 | `~:0(<method 'control' of 'select.kqueue' objects>)` |
| 4 | 0.0844 | 0.0003 | 333 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/events.py:92(_run)` |
| 5 | 0.0841 | 0.0003 | 333 | `~:0(<method 'run' of '_contextvars.Context' objects>)` |
| 6 | 0.0465 | 0.0007 | 74 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:550(start)` |
| 7 | 0.0357 | 0.0003 | 54 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:507(_handle_request)` |
| 8 | 0.0331 | 0.0004 | 564 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/traceback.py:437(extract)` |
| 9 | 0.0327 | 0.0088 | 564 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/traceback.py:459(_extract_from_extended_frame_gen)` |
| 10 | 0.0326 | 0.0004 | 54 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_app.py:527(_handle)` |

### N=30

| rank | cumulative sec | total sec | calls | function |
|---:|---:|---:|---:|---|
| 1 | 2.7901 | 0.0017 | 177 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/base_events.py:1966(_run_once)` |
| 2 | 2.6663 | 0.0005 | 177 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/selectors.py:540(select)` |
| 3 | 2.6657 | 2.6657 | 208 | `~:0(<method 'control' of 'select.kqueue' objects>)` |
| 4 | 0.1211 | 0.0003 | 444 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/asyncio/events.py:92(_run)` |
| 5 | 0.1208 | 0.0004 | 444 | `~:0(<method 'run' of '_contextvars.Context' objects>)` |
| 6 | 0.0757 | 0.0012 | 116 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:550(start)` |
| 7 | 0.0582 | 0.0005 | 86 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_protocol.py:507(_handle_request)` |
| 8 | 0.0530 | 0.0007 | 86 | `/private/tmp/loom-perf-venv/lib/python3.14/site-packages/aiohttp/web_app.py:527(_handle)` |
| 9 | 0.0483 | 0.0010 | 83 | `/Users/aleksanderarruda/dev/personal/gh/iterm2-loom/.loom/worktrees/ac43a0c9/loom/server.py:9740(handle_events)` |
| 10 | 0.0439 | 0.0005 | 765 | `/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/lib/python3.14/traceback.py:437(extract)` |

## Py-spy artifacts

| N | status | output |
|---:|---|---|
| 10 | missing | `tests/perf/artifacts/pyspy-n10.speedscope.json` |
| 20 | missing | `tests/perf/artifacts/pyspy-n20.speedscope.json` |
| 30 | missing | `tests/perf/artifacts/pyspy-n30.speedscope.json` |
