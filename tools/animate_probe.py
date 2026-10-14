"""
Image-to-video through a Hugging Face Space (HF_TOKEN, free ZeroGPU quota).

    MODE=describe python tools/animate_probe.py          # print the Space's API
    MODE=test IMAGE=x.jpg PROMPT="..." python tools/animate_probe.py out.mp4
"""

import os
import shutil
import sys

from gradio_client import Client, handle_file

SPACE = os.getenv("ANIMATE_SPACE", "kulkas2pintu/wan777")


def main():
    if os.getenv("MODE") == "engine":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        import animate_engine, time
        t = time.time()
        ok = animate_engine.animate(os.environ["IMAGE"], os.environ["PROMPT"], sys.argv[1],
                                    float(os.getenv("ANIM_SECONDS", "9")))
        print("OK" if ok else "FAILED", f"in {time.time() - t:.0f}s")
        sys.exit(0 if ok else 1)
    token = os.environ.get("HF_TOKEN") or None
    import inspect
    key = "token" if "token" in inspect.signature(Client).parameters else "hf_token"
    client = Client(SPACE, **{key: token})
    if os.getenv("MODE", "describe") == "describe":
        client.view_api(all_endpoints=True)
        for c in (client.config or {}).get("components", []):
            p = c.get("props") or {}
            if c.get("type") == "slider" or "minimum" in p:
                print("SLIDER", p.get("label"), "min", p.get("minimum"),
                      "max", p.get("maximum"), "value", p.get("value"))
        return
    api = os.environ["API_NAME"]
    args = eval(os.environ["ARGS"], {"handle_file": handle_file,
                                     "IMAGE": os.environ["IMAGE"],
                                     "PROMPT": os.environ["PROMPT"]})
    got = client.predict(*args, api_name=api)
    print("Result:", got)
    path = got
    if isinstance(got, (list, tuple)):
        path = got[0]
    if isinstance(path, dict):
        path = path.get("video") or path.get("path") or path.get("value")
        if isinstance(path, dict):
            path = path.get("path")
    shutil.copy(path, sys.argv[1])
    print("Saved", sys.argv[1])


if __name__ == "__main__":
    main()
