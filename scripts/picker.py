"""Diálogo nativo aislado del servidor y de su bucle de eventos."""

import json
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
kind = sys.argv[1]
if kind == "folder":
    value = filedialog.askdirectory(
        title="Seleccionar carpeta de imágenes", parent=root
    )
elif kind == "script":
    value = filedialog.askopenfilename(
        title="Seleccionar narración",
        filetypes=[("Narración TXT", "*.txt")],
        parent=root,
    )
else:
    value = filedialog.askopenfilename(
        title="Seleccionar muestra de voz",
        filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg"), ("Todos", "*.*")],
        parent=root,
    )
root.destroy()
print(json.dumps({"path": value}, ensure_ascii=True))
