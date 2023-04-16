# SimpleDiffusion

A Python UI for the `diffusers` library.  Inspired by InvokeAI, but uses less memory making it faster to run on a machine with low RAM (e.g. 16GB).  Developed and tested on macOS.  Other platforms could be supported with a small effort.

![Screenshot](docs/screenshot.webp)

### Installation

1. Open Terminal.
2. Navigate to the directory containing this project.
3. Create a virtual environment named `.venv` inside this directory and activate it:
    ```sh
    python3 -m venv .venv --prompt SimpleDiffusion
    ```
4. Activate the virtual environment (do it every time you run SimpleDiffusion)
    ```sh
    source .venv/bin/activate
    ```
5. Install dependencies.
    ```sh
    pip install -r requirements.txt
    ```
6. Run the GUI
    ```sh
    python simple_diffusion/main.py
    ```

### Features

- Text to Image
- Image to Image
- ControlNet 1.1 (Base, Img2Img)
- ESRGAN Upscaling
- GFPGAN Face Restoration

### Roadmap

Near-term:
- Multi-ControlNet
- UI design for multiple source images (e.g. inpainting masks, separate ControlNet images)
- Inpainting
- Async thumbnail loading
- Collapsing panels in UI
- Upscale/Restore Faces for existing images
- ControlNet preprocessor arguments
- Thumbnail paths by image hash 
- Long Prompt Weighting

Evaluate:
- Self-Attention Guidance
- MultiDiffusion

Requires new versions of dependencies:
- diffusers
  - LoRA (support is still minimal)
  - ControlNet Guess Mode
- torch
  - Retest float16 support
