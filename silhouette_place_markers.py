#!/usr/bin/env python3
"""Inkscape extension: Place Calibration Markers on a dedicated non-cut layer."""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0])))
if sys.platform.lower().startswith("win"):
    sys.path.append(r"C:\Program Files\Inkscape\share\inkscape\extensions")
elif sys.platform.lower().startswith("darwin"):
    sys.path.append("/Applications/Inkscape.app/Contents/Resources/share/inkscape/extensions")
else:
    sys.path.append("/usr/share/inkscape/extensions")

from inkex import EffectExtension, Boolean
from silhouette import Calibration


class PlaceCalibrationMarkers(EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--margin", type=float, dest="margin", default=6.5,
                          help="Distance from page edges [mm]")
        pars.add_argument("--diameter", type=float, dest="diameter", default=2.5,
                          help="Bullseye marker outer diameter [mm]")
        pars.add_argument("--replace", type=Boolean, dest="replace", default=True,
                          help="Replace existing calibration markers")

    def effect(self):
        Calibration.place_default_markers(
            self.svg,
            margin_mm=self.options.margin,
            diameter_mm=self.options.diameter,
            clear_existing=self.options.replace,
        )


if __name__ == "__main__":
    PlaceCalibrationMarkers().run()
