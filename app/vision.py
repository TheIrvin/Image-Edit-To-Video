"""Análisis visual básico en CPU; no identifica personajes ni interpreta el guion."""
from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .core import DATA, read_json, write_json


def analyze_image(path: Path):
    stat = path.stat()
    key = hashlib.sha256(f"vision-v1:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    cached = DATA / "cache" / "vision" / f"{key}.json"
    result = read_json(cached, None)
    if result is not None:
        return result
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        size = image.size
        image.thumbnail((640, 640))
        rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(28, 28))
    h, w = gray.shape
    if len(faces):
        x, y, fw, fh = max(faces, key=lambda face: face[2] * face[3])
        if len(faces) > 1:
            x, y = faces[:, 0].min(), faces[:, 1].min()
            fw = (faces[:, 0] + faces[:, 2]).max() - x
            fh = (faces[:, 1] + faces[:, 3]).max() - y
        point, method = ((x + fw / 2) / w, (y + fh / 2) / h), "face"
    else:
        # Residuo espectral: destacar regiones diferentes del contexto, con
        # suavizado espacial para no elegir un único borde o píxel brillante.
        small = cv2.resize(gray, (64, 64)).astype(np.float32)
        spectrum = np.fft.fft2(small)
        log = np.log(np.abs(spectrum) + 1e-8)
        residual = log - cv2.blur(log, (3, 3))
        saliency = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
        saliency = cv2.GaussianBlur(saliency.astype(np.float32), (0, 0), 4)
        yy, xx = np.mgrid[:64, :64]
        saliency *= 0.5 + 0.5 * np.exp(-((xx - 31.5)**2 + (yy - 31.5)**2) / (2 * 26**2))
        peak = float(saliency.max())
        weights = np.maximum(saliency - peak * 0.65, 0)
        if float(small.std()) < 3 or weights.sum() <= 1e-12:
            point, method = (0.5, 0.5), "center"
        else:
            point = (float((weights * (xx + 0.5)).sum() / weights.sum() / 64),
                     float((weights * (yy + 0.5)).sum() / weights.sum() / 64))
            method = "saliency"
    result = {"focus_x": point[0], "focus_y": point[1], "method": method,
              "faces": len(faces), "width": size[0], "height": size[1]}
    write_json(cached, result)
    return result


def visual_scene(source, scene):
    result = dict(scene)
    # Compatibilidad: puntos antiguos fuera del centro se consideran manuales.
    manual = scene.get("focus_manual", scene["focus_x"] != 0.5 or scene["focus_y"] != 0.5)
    analysis = analyze_image(source)
    if not manual:
        result.update(focus_x=analysis["focus_x"], focus_y=analysis["focus_y"])
    result["visual_analysis"] = analysis
    result["focus_manual"] = bool(manual)
    return result
