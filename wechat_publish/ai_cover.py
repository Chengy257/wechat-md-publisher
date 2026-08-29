"""AI-powered cover image generation via Gemini API."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

import requests

_DEFAULT_API_URL = "https://generativelanguage.googleapis.com"
# Current production-stable Gemini image model (the former
# "gemini-2.0-flash-exp" was an experimental preview and has been retired).
_DEFAULT_MODEL = "gemini-2.5-flash-image"


def _verify_image_bytes(image_bytes: bytes) -> None:
    """Decode *image_bytes* with Pillow when available; raise ValueError otherwise.

    The Gemini API might return text or corrupted bytes in an inlineData part;
    a header sniff is not enough, so the payload is actually decoded. When
    Pillow is not installed this check is skipped (same graceful degradation
    as compress_cover).
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dep in practice
        return

    from io import BytesIO

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img.verify()
    except Exception as e:
        raise ValueError(f"Gemini API returned invalid image data: {e}") from e


def _build_prompt(title: str, custom_prompt: str = "") -> str:
    if custom_prompt:
        return custom_prompt
    return (
        f"Create a clean, modern, minimalist cover image for a WeChat article "
        f"titled '{title}'. The image should be professional, eye-catching, "
        f"and suitable for social media. Use a 2.35:1 aspect ratio with "
        f"vibrant colors and simple geometric shapes. No text in the image."
    )


def generate_cover_image(
    title: str,
    output_path: Path,
    api_url: str,
    api_key: str,
    model: str = "",
    custom_prompt: str = "",
) -> Path:
    """Generate a cover image via Gemini API and save to output_path.

    Returns the saved image path. Raises on failure.
    """
    model = model or _DEFAULT_MODEL
    prompt = _build_prompt(title, custom_prompt)

    url = (
        f"{api_url.rstrip('/')}/v1beta/models/{model}:generateContent"
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            # Aspect ratio is controlled by the official API parameter, not
            # only by prompt text. 21:9 is the closest official tier to the
            # 2.35:1 cinematic cover format used for WeChat covers.
            "imageConfig": {"aspectRatio": "21:9"},
        },
    }

    resp = requests.post(
        url,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract image from response
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini API returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    image_data = None
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            mime = inline.get("mimeType") or inline.get("mime_type")
            if mime and not str(mime).startswith("image/"):
                raise ValueError(
                    f"Gemini API returned non-image data (mimeType={mime!r})"
                )
            image_data = inline["data"]
            break

    if not image_data:
        raise ValueError("Gemini API returned no image in response")

    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"Gemini API returned invalid base64 image data: {e}") from e
    if not image_bytes:
        raise ValueError("Gemini API returned an empty image")

    _verify_image_bytes(image_bytes)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    return output_path


def resolve_cover_ai_config(
    publish_config: dict, env: dict[str, str | None]
) -> tuple[str, str, str, str]:
    """Resolve AI cover config from publish.yaml and environment.

    Returns (api_url, api_key, model, custom_prompt). api_key may be empty.
    """
    ai_cfg = publish_config.get("ai", {})

    api_url = ai_cfg.get("cover_api_url", "") or _DEFAULT_API_URL
    model = ai_cfg.get("cover_model", "") or _DEFAULT_MODEL
    custom_prompt = ai_cfg.get("cover_prompt", "") or ""

    key_env_name = ai_cfg.get("cover_api_key_env", "GEMINI_API_KEY")
    api_key = env.get(key_env_name, "") or ""

    return api_url, api_key, model, custom_prompt
