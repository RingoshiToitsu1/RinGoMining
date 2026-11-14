"""Photo -> 3D print web app. Run `python app.py`, then open http://127.0.0.1:7860"""

import argparse
import re
import time
from pathlib import Path

import gradio as gr
from PIL import Image

from pipeline import PhotoTo3D
from printprep import PrintSettings, export_all, prepare_for_print

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
engine: PhotoTo3D | None = None


def make_model(
    photo,
    remove_bg,
    foreground_ratio,
    resolution,
    height_mm,
    flat_base,
    base_cut,
    smoothing,
    max_triangles,
    name,
    progress=gr.Progress(),
):
    if photo is None:
        raise gr.Error("Add a photo first.")
    t = time.time()
    progress(0.05, desc="Removing background")
    image = engine.preprocess(Image.fromarray(photo), remove_bg, foreground_ratio)

    progress(0.25, desc="Building 3D shape (the slow part)")
    raw = engine.generate(image, resolution=int(resolution))

    progress(0.8, desc="Making it printable")
    settings = PrintSettings(
        height_mm=height_mm,
        flat_base=flat_base,
        base_cut_percent=base_cut,
        smoothing=int(smoothing),
        max_triangles=int(max_triangles),
    )
    mesh, preview, report = prepare_for_print(raw, settings)

    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()) or time.strftime("model_%Y%m%d_%H%M%S")
    files = export_all(mesh, OUTPUT_DIR, stem, preview)
    info = report.as_markdown() + f"  \n**Time:** {time.time() - t:.0f}s on {engine.device}  \n**Saved to:** `{OUTPUT_DIR}`"
    return image, str(files["glb"]), str(files["3mf"]), str(files["stl"]), info


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Photo to 3D Print") as demo:
        gr.Markdown(
            "# Photo to 3D Print\n"
            "Upload a photo of one object, get a 3MF/STL ready for Bambu Studio. "
            "Best results: one object, plain background, shot slightly from above, nothing cut off."
        )
        with gr.Row():
            with gr.Column():
                photo = gr.Image(label="Photo", type="numpy", image_mode="RGBA", sources=["upload", "clipboard", "webcam"])
                name = gr.Textbox(label="File name (optional)", placeholder="e.g. bitcoin_miner")
                height = gr.Slider(10, 250, value=80, step=1, label="Print height (mm)")
                with gr.Accordion("Settings", open=False):
                    remove_bg = gr.Checkbox(value=True, label="Remove background")
                    fg_ratio = gr.Slider(0.5, 1.0, value=0.85, step=0.05, label="Object size in frame")
                    resolution = gr.Slider(128, 384, value=256, step=32, label="Detail (higher = sharper, slower, more memory)")
                    smoothing = gr.Slider(0, 20, value=5, step=1, label="Smoothing")
                    flat_base = gr.Checkbox(value=True, label="Flat base (cut the bottom so it sits on the plate)")
                    base_cut = gr.Slider(0, 20, value=3, step=0.5, label="Base cut (% of height)")
                    max_tris = gr.Slider(0, 1_000_000, value=200_000, step=50_000, label="Max triangles (0 = no limit)")
                go = gr.Button("Make 3D model", variant="primary")
            with gr.Column():
                processed = gr.Image(label="What the AI sees", interactive=False, height=220)
                viewer = gr.Model3D(label="3D preview (drag to rotate)", height=420)
                info = gr.Markdown()
                with gr.Row():
                    file_3mf = gr.File(label="3MF (open in Bambu Studio)")
                    file_stl = gr.File(label="STL")

        go.click(
            make_model,
            inputs=[photo, remove_bg, fg_ratio, resolution, height, flat_base, base_cut, smoothing, max_tris, name],
            outputs=[processed, viewer, file_3mf, file_stl, info],
        )
    return demo


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None, help="cuda:0 or cpu (default: auto)")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--share-on-network", action="store_true", help="let other devices on your Wi-Fi open it")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print("Loading TripoSR (first run downloads ~1.7 GB)...")
    engine = PhotoTo3D(device=args.device)
    print(f"Ready on {engine.device}.")
    if engine.device == "cpu":
        print("No NVIDIA GPU found: running on CPU. It works, just slower.")
    build_ui().queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0" if args.share_on_network else "127.0.0.1",
        server_port=args.port,
        inbrowser=not args.no_browser,
        allowed_paths=[str(OUTPUT_DIR)],
    )
