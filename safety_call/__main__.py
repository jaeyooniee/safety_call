"""python -m safety_call  ->  http://127.0.0.1:8000"""

import os

import uvicorn

from . import config

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    mode = "offline script" if config.offline_mode() else f"live Claude ({config.MODEL})"
    print(f"Safety Call: http://127.0.0.1:{port}  (agent: {mode})")
    uvicorn.run("safety_call.server:app", host="127.0.0.1", port=port)
