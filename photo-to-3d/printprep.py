"""Turn a raw generated mesh into something a slicer (Bambu Studio etc.) likes:
one closed solid, smoothed, a sane triangle count, a flat bottom, real size in mm,
sitting on the build plate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh


@dataclass
class PrintSettings:
    height_mm: float = 80.0  # final height of the print
    flat_base: bool = True  # slice the bottom off so it sits flat on the plate
    base_cut_percent: float = 3.0  # how much of the height to cut off for the base
    smoothing: int = 5  # Taubin smoothing passes (removes voxel "stair steps")
    remove_floaters: bool = True  # keep only the biggest piece
    max_triangles: int = 200_000  # 0 = don't simplify


@dataclass
class PrintReport:
    size_mm: tuple[float, float, float]
    triangles: int
    watertight: bool
    volume_cm3: float

    def as_markdown(self) -> str:
        x, y, z = self.size_mm
        ok = "Yes, ready to slice" if self.watertight else "No, Bambu Studio will offer to repair it"
        return (
            f"**Size:** {x:.1f} × {y:.1f} × {z:.1f} mm (W × D × H)  \n"
            f"**Solid volume:** {self.volume_cm3:.1f} cm³  \n"
            f"**Triangles:** {self.triangles:,}  \n"
            f"**Watertight:** {ok}"
        )


def _clean(mesh: trimesh.Trimesh, remove_floaters: bool) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    if remove_floaters:
        parts = mesh.split(only_watertight=False)
        if len(parts) > 1:
            mesh = max(parts, key=lambda p: abs(p.volume) if p.is_watertight else p.area)
    trimesh.repair.fix_normals(mesh)
    return mesh


def _simplify(mesh: trimesh.Trimesh, max_triangles: int) -> trimesh.Trimesh:
    if not max_triangles or len(mesh.faces) <= max_triangles:
        return mesh
    try:
        simplified = mesh.simplify_quadric_decimation(face_count=max_triangles)
    except Exception:  # fast_simplification missing or failed: keep full detail
        return mesh
    return simplified if simplified.is_watertight or not mesh.is_watertight else mesh


def _cut_flat_base(mesh: trimesh.Trimesh, cut_z: float) -> trimesh.Trimesh:
    lo, hi = mesh.bounds
    pad = 1.0 + 0.1 * float(np.max(hi - lo))
    keep = trimesh.creation.box(bounds=[[lo[0] - pad, lo[1] - pad, cut_z], [hi[0] + pad, hi[1] + pad, hi[2] + pad]])
    if mesh.is_watertight:
        try:
            result = trimesh.boolean.intersection([mesh, keep], engine="manifold")
            if len(result.faces):
                return result
        except Exception:
            pass
    # Fallback: plane slice with a cap.
    return trimesh.intersections.slice_mesh_plane(mesh, plane_normal=[0, 0, 1], plane_origin=[0, 0, cut_z], cap=True)


def _transfer_colors(target: trimesh.Trimesh, source: trimesh.Trimesh) -> trimesh.Trimesh:
    """Copy of `target` colored with the nearest vertex colors of `source`."""
    preview = target.copy()
    colors = getattr(source.visual, "vertex_colors", None)
    if colors is None or len(colors) != len(source.vertices):
        return preview
    from scipy.spatial import cKDTree

    _, idx = cKDTree(source.vertices).query(preview.vertices)
    preview.visual.vertex_colors = colors[idx]
    return preview


def prepare_for_print(
    mesh: trimesh.Trimesh, s: PrintSettings
) -> tuple[trimesh.Trimesh, trimesh.Trimesh, PrintReport]:
    """Returns (printable mesh, colored preview of it, report)."""
    mesh = _clean(mesh, s.remove_floaters)
    if s.smoothing > 0:
        trimesh.smoothing.filter_taubin(mesh, iterations=int(s.smoothing))
    color_ref = mesh.copy()  # keeps the photo's colors, for the on-screen preview
    mesh = _simplify(mesh, s.max_triangles)

    cut_frac = (s.base_cut_percent / 100.0) if s.flat_base else 0.0
    cut_frac = min(max(cut_frac, 0.0), 0.5)

    # Scale so that the height AFTER the base cut equals the requested height.
    raw_height = float(mesh.extents[2])
    scale = s.height_mm / (raw_height * (1.0 - cut_frac))
    mesh.apply_scale(scale)
    lift = [0, 0, -mesh.bounds[0][2]]
    mesh.apply_translation(lift)
    color_ref.apply_scale(scale)
    color_ref.apply_translation(lift)

    if cut_frac > 0:
        mesh = _cut_flat_base(mesh, cut_z=float(mesh.extents[2]) * cut_frac)

    # Center on the plate, bottom at z = 0.
    lo, hi = mesh.bounds
    center = [-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]
    mesh.apply_translation(center)
    color_ref.apply_translation(center)
    trimesh.repair.fix_normals(mesh)

    report = PrintReport(
        size_mm=tuple(float(v) for v in mesh.extents),
        triangles=len(mesh.faces),
        watertight=bool(mesh.is_watertight),
        volume_cm3=abs(float(mesh.volume)) / 1000.0 if mesh.is_watertight else 0.0,
    )
    return mesh, _transfer_colors(mesh, color_ref), report


def export_all(mesh: trimesh.Trimesh, out_dir: Path, name: str, color_preview: trimesh.Trimesh | None = None) -> dict[str, Path]:
    """Write STL + 3MF for the printer and a GLB for on-screen preview."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "stl": out_dir / f"{name}.stl",
        "3mf": out_dir / f"{name}.3mf",
        "glb": out_dir / f"{name}_preview.glb",
    }
    preview = (color_preview if color_preview is not None else mesh).copy()
    shape = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)  # print files: shape only
    shape.export(paths["stl"])
    shape.export(paths["3mf"])

    # GLB viewers are Y-up; rotate so the preview stands upright.
    # so the side facing the camera in the photo faces the viewer.
    preview.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    preview.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0]))
    preview.export(paths["glb"])
    return paths
