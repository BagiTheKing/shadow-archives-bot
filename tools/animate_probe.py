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
    client = Client(SPACE, hf_token=os.environ.get("HF_TOKEN") or None)
    if os.getenv("MODE", "describe") == "describe":
        client.view_api(all_endpoints=True)
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
