"""Photo -> 3D mesh using TripoSR (https://github.com/VAST-AI-Research/TripoSR).

We use TripoSR's model to turn one photo into a 3D density field, then run
marching cubes ourselves with scikit-image. That avoids TripoSR's
`torchmcubes` dependency (which has to be compiled and often fails on
Windows), and lets us pad the grid so every mesh comes out closed/watertight.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import torch
import trimesh
from PIL import Image
from skimage.measure import marching_cubes

HERE = Path(__file__).resolve().parent
TRIPOSR_DIR = HERE / "vendor" / "TripoSR"


def _import_triposr():
    if not (TRIPOSR_DIR / "tsr").is_dir():
        raise RuntimeError(
            f"TripoSR code not found in {TRIPOSR_DIR}. Run the setup script first."
        )
    if str(TRIPOSR_DIR) not in sys.path:
        sys.path.insert(0, str(TRIPOSR_DIR))
    # tsr imports torchmcubes at import time; we never call it, so stub it out
    # when it isn't installed.
    try:
        import torchmcubes  # noqa: F401
    except ImportError:
        stub = types.ModuleType("torchmcubes")

        def _unused(*_a, **_k):
            raise RuntimeError("torchmcubes is not used by this app")

        stub.marching_cubes = _unused
        sys.modules["torchmcubes"] = stub

    from tsr.system import TSR
    from tsr.utils import remove_background, resize_foreground

    return TSR, remove_background, resize_foreground


def pick_device(requested: str | None = None) -> str:
    if requested:
        return requested
    return "cuda:0" if torch.cuda.is_available() else "cpu"


class PhotoTo3D:
    def __init__(self, device: str | None = None, chunk_size: int = 8192):
        TSR, self._remove_bg, self._resize_fg = _import_triposr()
        self.device = pick_device(device)
        # Weights (~1.7 GB) are downloaded from Hugging Face on first run and cached.
        self.model = TSR.from_pretrained(
            "stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt"
        )
        self.model.renderer.set_chunk_size(chunk_size)
        self.model.to(self.device)
        self.model.eval()
        self._rembg_session = None

    # ---------------------------------------------------------------- image prep
    def preprocess(
        self, image: Image.Image, remove_background: bool = True, foreground_ratio: float = 0.85
    ) -> Image.Image:
        """Cut out the subject, center it, and put it on a grey background."""
        if not remove_background:
            return image.convert("RGB")
        if self._rembg_session is None:
            import rembg

            # isnet-general-use: small, Apache-2.0 licensed, good on objects
            self._rembg_session = rembg.new_session("isnet-general-use")
        image = self._remove_bg(image.convert("RGB"), self._rembg_session)
        image = self._resize_fg(image, foreground_ratio)
        arr = np.asarray(image).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        return Image.fromarray((arr * 255.0).astype(np.uint8))

    # ------------------------------------------------------------- 3D generation
    @torch.no_grad()
    def generate(self, image: Image.Image, resolution: int = 256, threshold: float = 25.0) -> trimesh.Trimesh:
        """Run TripoSR on a preprocessed image and return a closed, colored mesh.

        Output is Z-up, in TripoSR's units (roughly -0.87..0.87); printprep scales it to mm.
        """
        scene_codes = self.model([image], device=self.device)
        scene_code = scene_codes[0]
        radius = float(self.model.renderer.cfg.radius)

        lin = torch.linspace(-radius, radius, resolution)
        grid = torch.stack(torch.meshgrid(lin, lin, lin, indexing="ij"), dim=-1).reshape(-1, 3)
        density = self.model.renderer.query_triplane(
            self.model.decoder, grid.to(self.device), scene_code
        )["density_act"]
        volume = (density.reshape(resolution, resolution, resolution) - threshold).float().cpu().numpy()
        del density, grid

        if volume.max() <= 0:
            raise RuntimeError("The model didn't find a solid object in this photo.")

        # Pad with "empty space" so the surface is always closed, even if the
        # object touches the edge of the grid.
        volume = np.pad(volume, 1, mode="constant", constant_values=-threshold)
        verts, faces, _normals, _ = marching_cubes(volume, level=0.0)
        verts = (verts - 1.0) / (resolution - 1.0) * (2 * radius) - radius

        colors = self.model.renderer.query_triplane(
            self.model.decoder,
            torch.from_numpy(verts.astype(np.float32)).to(self.device),
            scene_code,
        )["color"]
        colors = (colors.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)

        mesh = trimesh.Trimesh(vertices=verts, faces=faces, vertex_colors=colors, process=True)
        trimesh.repair.fix_normals(mesh)
        return mesh
