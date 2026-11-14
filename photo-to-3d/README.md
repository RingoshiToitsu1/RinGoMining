# Photo to 3D Print

Upload a photo, get a 3MF/STL file ready for Bambu Studio. It runs on your own computer, free, with no per-model fees.

It uses **TripoSR**, the open-source model Tripo AI released (MIT license). The app then cleans the model up for printing: it keeps one solid piece, smooths it, gives it a flat base, scales it to the height you pick in mm and sets it on the build plate.

## What you need

- A Windows PC (Mac/Linux work too).
- **Best:** an NVIDIA graphics card with 6 GB+ of memory (RTX 2060 or newer). A model takes a few seconds.
- **No NVIDIA card?** It still works on the CPU, about 30–60 seconds per photo.
- About 6 GB of free disk space.

## Setup (one time, Windows)

1. Install **Python 3.11** from <https://www.python.org/downloads/>. On the first install screen, tick **"Add python.exe to PATH"**.
2. Optional: install **Git** from <https://git-scm.com/download/win>. Setup works without it.
3. Copy this `photo-to-3d` folder somewhere on your PC, e.g. `C:\photo-to-3d`.
4. Double-click **`setup.bat`**. It finds your NVIDIA card if you have one and installs everything (5–15 minutes).

Mac/Linux: run `./setup.sh` instead.

## Use it

1. Double-click **`start.bat`** (Mac/Linux: `./start.sh`). Your browser opens to the app.
   The first start downloads the AI model (~1.7 GB). After that it's quick.
2. Drop in a photo, set the **print height in mm**, then click **Make 3D model**.
3. Check the 3D preview, then download the **3MF** and open it in Bambu Studio. Your files are also saved in the `output` folder.

To use it from your phone or another computer on the same Wi‑Fi, start it with `start.bat --share-on-network`, then open `http://<your-PC's-IP>:7860`.

Doing a batch of photos? Use the command line:

```
.venv\Scripts\python cli.py photo1.jpg photo2.jpg --height 100
```

## Tips for good results

- **One object, plain background, whole object in the frame.** Background removal does the rest.
- Shoot from slightly above, the way you'd look at it on a table.
- Chunky objects (figures, toys, statues, products) work best. Very thin parts, like wires, straps or long legs at small sizes, come out lumpy or too thin to print.
- The AI has to guess the back of the object. If the back matters, check it in the preview.
- At small heights, thin parts like legs may come out under 1–2 mm. Print bigger, or turn on supports in Bambu Studio.

## Settings

| Setting | What it does |
| --- | --- |
| Print height | Final height in mm, after the base cut. |
| Remove background | Turn off only if your photo already has a clean or transparent background. |
| Object size in frame | How much of the frame the object fills before the AI sees it. 0.85 is a good default. |
| Detail | Mesh resolution. 256 is good. 320–384 is sharper but slower and needs more memory. |
| Smoothing | Removes the small "stair-step" texture. Raise it for a smoother look, lower it for sharp details. |
| Flat base / Base cut | Slices a little off the bottom so it sits flat on the plate. |
| Max triangles | Keeps files a sensible size. 200k is plenty for FDM printing. |

## Troubleshooting

- **"CUDA out of memory"**: lower Detail to 192 or 128, or run `start.bat --device cpu`.
- **It says "CPU" but you have an NVIDIA card**: update your NVIDIA driver, delete the `.venv` folder and run `setup.bat` again.
- **Bad cut-out** (part of the background became part of the model): crop the photo tighter, or remove the background yourself first (phone photo apps can do this) and untick "Remove background".

## Files

- `app.py`: the browser app
- `cli.py`: command-line version
- `pipeline.py`: photo → 3D, using TripoSR. Runs marching cubes itself, so the hard-to-install `torchmcubes` isn't needed.
- `printprep.py`: makes the mesh printable and exports STL/3MF
- `install.py`, `setup.bat`/`setup.sh`, `start.bat`/`start.sh`: setup and launch scripts
