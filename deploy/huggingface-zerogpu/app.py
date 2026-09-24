"""La Cigogne D'Ailleurs — Hugging Face ZeroGPU validation adapter.

This is a cloud inference validation Space, not a replacement for the main
FastAPI service. It reuses server/main.py's existing model functions so the
same AI implementation is exercised on a cloud GPU.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

import gradio as gr
import spaces
from PIL import Image
from starlette.datastructures import UploadFile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import main as ai  # noqa: E402


def _upload(image: Image.Image, name: str = "room.png") -> UploadFile:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return UploadFile(filename=name, file=buf)


def _decode_data_url(value: str | None) -> Image.Image | None:
    if not value:
        return None
    if "," in value:
        value = value.split(",", 1)[1]
    import base64
    return Image.open(io.BytesIO(base64.b64decode(value))).convert("RGB")


@spaces.GPU(duration=120)
def analyze_room(image: Image.Image):
    if image is None:
        raise gr.Error("Importez une photo de pièce.")
    import json
    result = asyncio.run(ai.analyze(_upload(image)))
    return json.loads(result.body.decode())


@spaces.GPU(duration=120)
def select_object(image: Image.Image, x: int, y: int):
    if image is None:
        raise gr.Error("Importez une photo.")
    import json
    result = asyncio.run(ai.select_mask(_upload(image), int(x), int(y)))
    return json.loads(result.body.decode())


@spaces.GPU(duration=120)
def remove_object(image: Image.Image, x: int, y: int):
    if image is None:
        raise gr.Error("Importez une photo.")
    result = asyncio.run(ai.remove(_upload(image), int(x), int(y), False))
    import json, base64
    payload = json.loads(result.body.decode())
    value = payload.get("image")
    if value and "," in value:
        value = value.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(value))).convert("RGB"), payload


with gr.Blocks(title="La Cigogne D'Ailleurs — ZeroGPU") as demo:
    gr.Markdown("# La Cigogne D'Ailleurs — Cloud AI validation\nCC0 demo furniture assets + existing AI pipeline.")
    with gr.Tab("Analyze"):
        image = gr.Image(type="pil", label="Room photo")
        out = gr.JSON(label="Analysis response")
        btn = gr.Button("Analyze room")
        btn.click(analyze_room, image, out)
    with gr.Tab("Remove object"):
        image2 = gr.Image(type="pil", label="Room photo")
        with gr.Row():
            x = gr.Number(value=500, precision=0, label="X")
            y = gr.Number(value=500, precision=0, label="Y")
        result_img = gr.Image(type="pil", label="AI result")
        result_meta = gr.JSON(label="Quality metadata")
        gr.Button("Remove with AI").click(remove_object, [image2, x, y], [result_img, result_meta])
    gr.Markdown("Cloud GPU: Hugging Face ZeroGPU. Physical GPU/model validation must be performed after deployment in the Space.")

if __name__ == "__main__":
    demo.launch()
