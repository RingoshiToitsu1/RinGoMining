"""Command-line version: python cli.py photo.jpg [more.jpg ...] --height 80"""

import argparse
import time
from pathlib import Path

from PIL import Image

from pipeline import PhotoTo3D
from printprep import PrintSettings, export_all, prepare_for_print


def main():
    ap = argparse.ArgumentParser(description="Turn photos into printable STL/3MF files.")
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--height", type=float, default=80.0, help="final print height in mm")
    ap.add_argument("--resolution", type=int, default=256, help="mesh detail (128-384)")
    ap.add_argument("--smoothing", type=int, default=5)
    ap.add_argument("--base-cut", type=float, default=3.0, help="percent of height cut off for a flat base")
    ap.add_argument("--no-flat-base", action="store_true")
    ap.add_argument("--keep-background", action="store_true", help="photo already has a clean/transparent background")
    ap.add_argument("--foreground-ratio", type=float, default=0.85)
    ap.add_argument("--max-triangles", type=int, default=200_000)
    ap.add_argument("--device", default=None, help="cuda:0 or cpu (default: auto)")
    args = ap.parse_args()

    t = time.time()
    engine = PhotoTo3D(device=args.device)
    print(f"Model loaded on {engine.device} in {time.time() - t:.0f}s")

    settings = PrintSettings(
        height_mm=args.height,
        flat_base=not args.no_flat_base,
        base_cut_percent=args.base_cut,
        smoothing=args.smoothing,
        max_triangles=args.max_triangles,
    )
    for path in args.images:
        t = time.time()
        image = engine.preprocess(Image.open(path), not args.keep_background, args.foreground_ratio)
        raw = engine.generate(image, resolution=args.resolution)
        mesh, preview, report = prepare_for_print(raw, settings)
        files = export_all(mesh, args.out, path.stem, preview)
        image.save(args.out / f"{path.stem}_input.png")
        print(f"\n{path.name} done in {time.time() - t:.0f}s")
        print(report.as_markdown().replace("**", "").replace("  \n", "\n"))
        for kind, p in files.items():
            print(f"  {kind}: {p}")


if __name__ == "__main__":
    main()
