#!/usr/bin/env python3
"""Inkscape extension: Reset / Undo scanner calibration metadata non-destructively."""

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


class ResetCalibration(EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--remove_markers", "--remove-markers", type=Boolean, dest="remove_markers", default=False,
                          help="Also remove the Calibration Markers layer")

    def effect(self):
        Calibration.clear_calibration(self.svg, remove_markers=self.options.remove_markers)


if __name__ == "__main__":
    ResetCalibration().run()
