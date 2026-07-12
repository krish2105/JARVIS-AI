"""'Look at my screen' — capture the screen and answer questions about it with
a local vision-language model (mlx-vlm on Apple Silicon). Nothing leaves the
machine.

Screen capture needs macOS Screen Recording permission; if it isn't granted the
capture returns a black frame, and we tell the user how to grant it instead of
feeding the model a blank image.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("jarvis.vision")

# Small vision-language model — good quality, ~2 GB, runs locally via MLX.
VLM_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"

_vlm = None
_lock = threading.Lock()

_NO_PERMISSION = (
    "I couldn't capture the screen. Grant Screen Recording permission in System "
    "Settings › Privacy & Security › Screen Recording (allow Jarvis), then try again."
)


def _get_vlm():
    global _vlm
    with _lock:
        if _vlm is None:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            model, processor = load(VLM_MODEL)
            config = load_config(VLM_MODEL)
            _vlm = (model, processor, config)
    return _vlm


def _capture_screen() -> Path | None:
    tmp = Path(tempfile.gettempdir()) / "jarvis_screen.png"
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", str(tmp)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # noqa: BLE001
        logger.exception("screencapture failed")
        return None
    if not tmp.exists() or tmp.stat().st_size < 2000:
        return None  # missing or a tiny/black frame -> no permission
    return tmp


def look_at_screen(question: str) -> str:
    question = (question or "").strip() or "Describe what is on the screen."
    image = _capture_screen()
    if image is None:
        return _NO_PERMISSION
    try:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        model, processor, config = _get_vlm()
        prompt = apply_chat_template(processor, config, question, num_images=1)
        result = generate(
            model, processor, prompt, image=[str(image)], max_tokens=300, verbose=False,
        )
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        return text.strip() or "I looked but couldn't describe it."
    except Exception as e:  # noqa: BLE001
        logger.exception("vision inference failed")
        return f"Error analyzing the screen: {e}"
