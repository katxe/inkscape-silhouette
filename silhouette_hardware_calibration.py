#!/usr/bin/env python3
"""Inkscape extension: Hardware Calibration Wizard (generate test pattern or measure post-cut scan)."""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0])))
if sys.platform.lower().startswith("win"):
    sys.path.append(r"C:\Program Files\Inkscape\share\inkscape\extensions")
elif sys.platform.lower().startswith("darwin"):
    sys.path.append("/Applications/Inkscape.app/Contents/Resources/share/inkscape/extensions")
else:
    sys.path.append("/usr/share/inkscape/extensions")

import numpy as np
from inkex import EffectExtension
from silhouette import Calibration, HardwareCalibration


class HardwareOffsetMeasureApp:
    """Interactive tkinter + PIL window to measure printed target vs cut-out square center."""

    def __init__(self, user_config):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from PIL import Image, ImageTk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.Image = Image
        self.ImageTk = ImageTk

        self.user_config = user_config
        self.pil_image = None
        self.tk_photo = None
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_start = None
        self.drag_idx = None

        # Two draggable points: index 0 = Printed Target Center, index 1 = Cut-Out Square Center
        self.points_px = []

        self.root = tk.Tk()
        self.root.title("Hardware Calibration: Measure Post-Cut Offset")
        self.root.geometry("1050x780")
        self._build_ui()

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        top = ttk.Frame(self.root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Load Post-Cut Scan...", command=self._load_scan).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Rotate 180", command=self._rotate_180).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Fit View", command=self._fit_view).pack(side=tk.LEFT, padx=4)

        ttk.Label(
            top,
            text="Drag CYAN crosshair onto PRINTED target center, and RED crosshair onto CUT-OUT hole center.",
        ).pack(side=tk.LEFT, padx=12)

        self.canvas = tk.Canvas(self.root, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", self._on_left_down)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_up)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_drag)

        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Label(bottom, text="Scan DPI:").pack(side=tk.LEFT, padx=(4, 2))
        self.dpi_var = tk.StringVar(value="300.0")
        dpi_entry = ttk.Entry(bottom, textvariable=self.dpi_var, width=7)
        dpi_entry.pack(side=tk.LEFT, padx=2)
        dpi_entry.bind("<FocusOut>", lambda _e: self._update_status())

        self.status_var = tk.StringVar(value="Load your post-cut scan (with dark backing sheet).")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        ttk.Button(bottom, text="Close", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="Save New System Offset", command=self._save_offset).pack(side=tk.RIGHT, padx=4)

    def _get_dpi(self):
        try:
            v = float(self.dpi_var.get())
            return v if v > 1.0 else 300.0
        except ValueError:
            return 300.0

    def _load_scan(self):
        initial_dir = self.user_config.get("last_scan_dir") or os.path.expanduser("~")
        path = self.filedialog.askopenfilename(
            title="Select Post-Cut Scanned Image",
            initialdir=initial_dir,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("All files", "*.*")],
        )
        if not path:
            return

        self.user_config["last_scan_dir"] = os.path.dirname(path)
        dpi_x, _ = Calibration.read_image_dpi(path, default_dpi=300.0)
        self.dpi_var.set(f"{dpi_x:.1f}")

        self.pil_image = self.Image.open(path).convert("RGB")
        w, h = self.pil_image.size
        self.points_px = [[w * 0.5 - 20.0, h * 0.5], [w * 0.5 + 20.0, h * 0.5]]
        self._fit_view()
        self._update_status()

    def _rotate_180(self):
        if self.pil_image is None:
            return
        self.pil_image = self.pil_image.rotate(180)
        w, h = self.pil_image.size
        for pt in self.points_px:
            pt[0] = w - pt[0]
            pt[1] = h - pt[1]
        self._redraw()
        self._update_status()

    def _fit_view(self):
        if self.pil_image is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.size
        self.zoom = min(cw / iw, ch / ih) * 0.95
        self.pan_x = (cw - iw * self.zoom) / 2.0
        self.pan_y = (ch - ih * self.zoom) / 2.0
        self._redraw()

    def _img_to_canvas(self, ix, iy):
        return ix * self.zoom + self.pan_x, iy * self.zoom + self.pan_y

    def _canvas_to_img(self, cx, cy):
        return (cx - self.pan_x) / self.zoom, (cy - self.pan_y) / self.zoom

    def _on_mousewheel(self, event):
        if self.pil_image is None:
            return
        factor = 1.15 if event.delta > 0 else (1.0 / 1.15)
        ix, iy = self._canvas_to_img(event.x, event.y)
        self.zoom = max(0.05, min(20.0, self.zoom * factor))
        self.pan_x = event.x - ix * self.zoom
        self.pan_y = event.y - iy * self.zoom
        self._redraw()

    def _on_pan_start(self, event):
        self.pan_start = (event.x, event.y, self.pan_x, self.pan_y)

    def _on_pan_drag(self, event):
        if self.pan_start is None:
            return
        sx, sy, px0, py0 = self.pan_start
        self.pan_x = px0 + (event.x - sx)
        self.pan_y = py0 + (event.y - sy)
        self._redraw()

    def _on_left_down(self, event):
        if self.pil_image is None:
            return
        best_i = None
        best_d = 18.0
        for idx, (ix, iy) in enumerate(self.points_px):
            cx, cy = self._img_to_canvas(ix, iy)
            d = ((cx - event.x) ** 2 + (cy - event.y) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_i = idx
        self.drag_idx = best_i

    def _on_left_drag(self, event):
        if self.drag_idx is None or self.pil_image is None:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        w, h = self.pil_image.size
        self.points_px[self.drag_idx] = [max(0.0, min(w, ix)), max(0.0, min(h, iy))]
        self._redraw()
        self._update_status()

    def _on_left_up(self, _event):
        self.drag_idx = None

    def _redraw(self):
        self.canvas.delete("all")
        if self.pil_image is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        iw, ih = self.pil_image.size
        x0 = max(0, int(np.floor((0 - self.pan_x) / self.zoom)))
        y0 = max(0, int(np.floor((0 - self.pan_y) / self.zoom)))
        x1 = min(iw, int(np.ceil((cw - self.pan_x) / self.zoom)))
        y1 = min(ih, int(np.ceil((ch - self.pan_y) / self.zoom)))
        if x1 > x0 and y1 > y0:
            crop = self.pil_image.crop((x0, y0, x1, y1))
            disp_w = max(1, int(round((x1 - x0) * self.zoom)))
            disp_h = max(1, int(round((y1 - y0) * self.zoom)))
            resized = crop.resize((disp_w, disp_h), self.Image.NEAREST if self.zoom > 3.0 else self.Image.BILINEAR)
            self.tk_photo = self.ImageTk.PhotoImage(resized)
            dx, dy = self._img_to_canvas(x0, y0)
            self.canvas.create_image(dx, dy, anchor="nw", image=self.tk_photo)

        specs = [("#00ffff", "PRINTED"), ("#ff3333", "CUT HOLE")]
        for (color, label), (ix, iy) in zip(specs, self.points_px):
            cx, cy = self._img_to_canvas(ix, iy)
            r = 12
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2)
            self.canvas.create_line(cx - r - 6, cy, cx + r + 6, cy, fill=color, width=1)
            self.canvas.create_line(cx, cy - r - 6, cx, cy + r + 6, fill=color, width=1)
            self.canvas.create_text(cx + r + 8, cy - r, text=label, fill=color, anchor="w", font=("Arial", 10, "bold"))

    def _compute_offsets(self):
        dpi = self._get_dpi()
        cur_x = float(self.user_config.get("offset_x_mm", Calibration.DEFAULT_OFFSET_X_MM))
        cur_y = float(self.user_config.get("offset_y_mm", Calibration.DEFAULT_OFFSET_Y_MM))
        return HardwareCalibration.compute_hardware_offset(
            self.points_px[0], self.points_px[1], dpi, dpi, cur_x, cur_y
        )

    def _update_status(self):
        if len(self.points_px) < 2:
            return
        new_x, new_y, dx, dy = self._compute_offsets()
        self.status_var.set(f"Measured Delta: dX={dx:+.3f} mm, dY={dy:+.3f} mm  ->  New System Offset: X={new_x:.3f} mm, Y={new_y:.3f} mm")

    def _save_offset(self):
        if len(self.points_px) < 2:
            self.messagebox.showwarning("No Scan", "Please load a post-cut scan first.")
            return
        new_x, new_y, dx, dy = self._compute_offsets()
        self.user_config["offset_x_mm"] = round(new_x, 4)
        self.user_config["offset_y_mm"] = round(new_y, 4)
        Calibration.save_user_config(self.user_config)
        self.messagebox.showinfo(
            "System Offset Saved",
            f"Saved new System Offset:\nX = {new_x:.3f} mm (delta {dx:+.3f} mm)\nY = {new_y:.3f} mm (delta {dy:+.3f} mm)",
        )
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class HardwareCalibrationExtension(EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--action", type=str, dest="action", default="generate",
                          help="generate or measure")
        pars.add_argument("--margin", type=float, dest="margin", default=6.5,
                          help="Corner marker margin [mm]")
        pars.add_argument("--square-size", type=float, dest="square_size", default=15.0,
                          help="Target cut-out square size [mm]")

    def effect(self):
        if self.options.action == "generate":
            HardwareCalibration.generate_test_pattern(
                self.svg,
                margin_mm=self.options.margin,
                square_size_mm=self.options.square_size,
            )
        else:
            user_cfg = Calibration.load_user_config()
            app = HardwareOffsetMeasureApp(user_cfg)
            app.run()


if __name__ == "__main__":
    HardwareCalibrationExtension().run()
