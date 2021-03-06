from itertools import chain
import sys
from collections import defaultdict
import gc
import random
import warnings
import math
import collections

from AnyQt.QtWidgets import QWidget, QGraphicsItem, QPushButton, QMenu, \
    QGridLayout, QFormLayout, QAction, QVBoxLayout, QApplication, QWidgetAction, QLabel, QGraphicsView, QGraphicsScene, QSplitter
from AnyQt.QtGui import QColor, QPixmapCache, QPen, QKeySequence
from AnyQt.QtCore import Qt, QRectF

import numpy as np
import pyqtgraph as pg
from pyqtgraph.graphicsItems.ViewBox import ViewBox
from pyqtgraph import Point, GraphicsObject

from Orange.canvas.registry.description import Default
import Orange.data
from Orange.widgets.widget import OWWidget, Msg, OWComponent
from Orange.widgets import gui
from Orange.widgets.visualize.owheatmap import GraphicsHeatmapWidget, GraphicsWidget
from Orange.widgets.settings import \
    Setting, ContextSetting, DomainContextHandler, SettingProvider
from Orange.widgets.utils.itemmodels import VariableListModel
from Orange.widgets.utils.colorpalette import ColorPaletteGenerator
from Orange.widgets.utils.plot import \
    SELECT, PANNING, ZOOMING
from Orange.widgets.utils.itemmodels import DomainModel
from Orange.widgets.visualize.owheatmap import color_palette_table

from orangecontrib.infrared.data import getx
from orangecontrib.infrared.widgets.line_geometry import \
    distance_curves, intersect_curves_chunked
from orangecontrib.infrared.widgets.gui import lineEditFloatOrNone

from orangecontrib.infrared.preprocess import Integrate

from orangecontrib.infrared.widgets.owcurves import InteractiveViewBox, \
    MenuFocus, CurvePlot, SELECTONE
from orangecontrib.infrared.widgets.owpreproc import MovableVlineWD


def values_to_linspace(vals):
    """Find a near maching linspace for the values given.
    The problem is that some values can be missing and
    that they are inexact. The minumum and maximum values
    are kept as limits."""
    vals = vals[~np.isnan(vals)]
    if len(vals):
        vals = np.unique(vals)
        if len(vals) == 1:
            return vals[0], vals[0], 1
        minabsdiff = (vals[-1] - vals[0])/(len(vals)*100)
        diffs = np.diff(vals)
        diffs = diffs[diffs > minabsdiff]
        first_valid = diffs[0]
        # allow for a percent mismatch
        diffs = diffs[diffs < first_valid*1.01]
        step = np.mean(diffs)
        size = int(round((vals[-1]-vals[0])/step) + 1)
        return vals[0], vals[-1], size
    return None


def index_values(vals, linspace):
    """ Remap values into index of array defined by linspace. """
    v = (vals - linspace[0])*(linspace[2] - 1)/(linspace[1] - linspace[0])
    return np.round(v).astype(int)


def get_levels(img):
    """ Compute levels. Account for NaN values. """
    while img.size > 2 ** 16:
        img = img[::2, ::2]
    mn, mx = np.nanmin(img), np.nanmax(img)
    if mn == mx:
        mn = 0
        mx = 255
    return [mn, mx]


class ImageItemNan(pg.ImageItem):
    """ Simplified ImageItem that can show NaN color. """

    def render(self):
        # simplified pg.ImageITem

        if self.image is None or self.image.size == 0:
            return
        if isinstance(self.lut, collections.Callable):
            lut = self.lut(self.image)
        else:
            lut = self.lut

        image = self.image
        levels = self.levels

        if self.axisOrder == 'col-major':
            image = image.transpose((1, 0, 2)[:image.ndim])

        argb, alpha = pg.makeARGB(image, lut=lut, levels=levels)
        argb[np.isnan(image)] = (100, 100, 100, 255)  # replace unknown values with a color
        self.qimage = pg.makeQImage(argb, alpha, transpose=False)


class ImagePlot(QWidget, OWComponent):

    attr_x = ContextSetting(None)
    attr_y = ContextSetting(None)
    gamma = Setting(0)
    threshold_low = Setting(0.0)
    threshold_high = Setting(1.0)

    def __init__(self, parent):
        QWidget.__init__(self)
        OWComponent.__init__(self, parent)

        self.parent = parent

        self.selection_enabled = False

        self.plotview = pg.PlotWidget(background="w", viewBox=InteractiveViewBox(self))
        self.plot = self.plotview.getPlotItem()

        layout = QVBoxLayout()
        self.setLayout(layout)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self.plotview)

        self.img = ImageItemNan()
        self.img.setOpts(axisOrder='row-major')
        self.plot.addItem(self.img)
        self.plot.vb.setAspectLocked()

        layout = QGridLayout()
        self.plotview.setLayout(layout)
        self.button = QPushButton("View", self.plotview)
        self.button.setAutoDefault(False)

        layout.setRowStretch(1, 1)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self.button, 0, 0)
        view_menu = MenuFocus(self)
        self.button.setMenu(view_menu)

        common_options = dict(
            labelWidth=50, orientation=Qt.Horizontal, sendSelectedValue=True,
            valueType=str)

        choose_xy = QWidgetAction(self)
        box = gui.vBox(self)
        box.setFocusPolicy(Qt.TabFocus)
        self.xy_model = DomainModel(DomainModel.METAS | DomainModel.CLASSES, valid_types=DomainModel.PRIMITIVE)
        self.models = [self.xy_model]
        self.cb_attr_x = gui.comboBox(
            box, self, "attr_x", label="Axis x:", callback=self.update_attr,
            model=self.xy_model, **common_options)
        self.cb_attr_y = gui.comboBox(
            box, self, "attr_y", label="Axis y:", callback=self.update_attr,
            model=self.xy_model, **common_options)
        box.setFocusProxy(self.cb_attr_x)

        form = QFormLayout(
            formAlignment=Qt.AlignLeft,
            labelAlignment=Qt.AlignLeft,
            fieldGrowthPolicy=QFormLayout.AllNonFixedFieldsGrow
        )

        lowslider = gui.hSlider(
            box, self, "threshold_low", minValue=0.0, maxValue=1.0,
            step=0.05, ticks=True, intOnly=False,
            createLabel=False, callback=self.update_color_schema)
        highslider = gui.hSlider(
            box, self, "threshold_high", minValue=0.0, maxValue=1.0,
            step=0.05, ticks=True, intOnly=False,
            createLabel=False, callback=self.update_color_schema)
        gammaslider = gui.hSlider(
            box, self, "gamma", minValue=0.0, maxValue=20.0,
            step=1.0, ticks=True, intOnly=False,
            createLabel=False, callback=self.update_color_schema
        )

        form.addRow("Low:", lowslider)
        form.addRow("High:", highslider)
        form.addRow("Gamma:", gammaslider)

        box.layout().addLayout(form)

        choose_xy.setDefaultWidget(box)
        view_menu.addAction(choose_xy)

        self.markings_integral = []

        self.data = None

    def update_color_schema(self):
        if not self.threshold_low < self.threshold_high:
            # TODO this belongs here, not in the parent
            self.parent.Warning.threshold_error()
            return
        else:
            self.parent.Warning.threshold_error.clear()
        # TODO add color chooser
        colors = [(0, 0, 255), (255, 255, 0)]
        cols = color_palette_table(
            colors, threshold_low=self.threshold_low,
            threshold_high=self.threshold_high,
            gamma=self.gamma)
        self.img.setLookupTable(cols)

    def update_attr(self):
        self.show_data()

    def init_attr_values(self):
        domain = self.data and self.data.domain
        for model in self.models:
            model.set_domain(domain)
        self.attr_x = self.xy_model[0] if self.xy_model else None
        self.attr_y = self.xy_model[1] if len(self.xy_model) >= 2 \
            else self.attr_x

    def set_data(self, data):
        self.img.clear()
        if data is not None:
            same_domain = (self.data and
                           data.domain.checksum() == self.data.domain.checksum())
            self.data = data
            if not same_domain:
                self.init_attr_values()
        self.show_data()

    def set_integral_limits(self):
        self.show_data()

    def refresh_markings(self, di):

        for m in self.markings_integral:
            self.parent.curveplot.remove_marking(m)
        self.markings_integral = []

        color = Qt.red

        def add_marking(a):
            self.markings_integral.append(a)
            self.parent.curveplot.add_marking(a)

        if "baseline" in di:
            bs_x, bs_ys = di["baseline"]
            baseline = pg.PlotCurveItem()
            baseline.setPen(pg.mkPen(color=QColor(color), width=2, style=Qt.DotLine))
            baseline.setZValue(10)
            baseline.setData(x=bs_x, y=bs_ys[0])
            add_marking(baseline)

        if "curve" in di:
            bs_x, bs_ys = di["curve"]
            curve = pg.PlotCurveItem()
            curve.setPen(pg.mkPen(color=QColor(color), width=2))
            curve.setZValue(10)
            curve.setData(x=bs_x, y=bs_ys[0])
            add_marking(curve)

        if "fill" in di:
            (x1, ys1), (x2, ys2) = di["fill"]
            phigh = pg.PlotCurveItem(x1, ys1[0], pen=None)
            plow = pg.PlotCurveItem(x2, ys2[0], pen=None)
            color = QColor(color)
            color.setAlphaF(0.5)
            cc = pg.mkBrush(color)
            pfill = pg.FillBetweenItem(plow, phigh, brush=cc)
            pfill.setZValue(9)
            add_marking(pfill)

        if "line" in di:
            (x1, y1), (x2, y2) = di["line"]
            line = pg.PlotCurveItem()
            line.setPen(pg.mkPen(color=QColor(color), width=4))
            line.setZValue(10)
            line.setData(x=[x1[0], x2[0]], y=[y1[0], y2[0]])
            add_marking(line)

    def show_data(self):
        self.img.clear()
        if self.data:
            xat = self.data.domain[self.attr_x]
            yat = self.data.domain[self.attr_y]

            ndom = Orange.data.Domain([xat, yat])
            datam = Orange.data.Table(ndom, self.data)
            coorx = datam.X[:, 0]
            coory = datam.X[:, 1]
            lsx = values_to_linspace(coorx)
            lsy = values_to_linspace(coory)

            l1, l2 = self.parent.lowlim, self.parent.highlim

            gx = getx(self.data)

            if l1 is None:
                l1 = min(gx) - 1
            if l2 is None:
                l2 = max(gx) + 1

            l1, l2 = min(l1, l2), max(l1, l2)

            imethod = self.parent.integration_methods[self.parent.integration_method]
            datai = Integrate(method=imethod, limits=[[l1, l2]])(self.data)

            di = {}
            if self.parent.curveplot.selected_indices:
                ind = list(self.parent.curveplot.selected_indices)[0]
                di = datai.domain.attributes[0].compute_value.draw_info(self.data[ind:ind+1])
            self.refresh_markings(di)

            d = datai.X[:, 0]

            # set data
            imdata = np.ones((lsy[2], lsx[2]))*float("nan")
            xindex = index_values(coorx, lsx)
            yindex = index_values(coory, lsy)
            imdata[yindex, xindex] = d

            levels = get_levels(imdata)
            self.update_color_schema()

            self.img.setImage(imdata, levels=levels)

            # shift centres of the pixels so that the axes are useful
            shiftx = (lsx[1]-lsx[0])/(2*(lsx[2]-1))
            shifty = (lsy[1]-lsy[0])/(2*(lsy[2]-1))
            left = lsx[0] - shiftx
            bottom = lsy[0] - shifty
            width = (lsx[1]-lsx[0]) + 2*shiftx
            height = (lsy[1]-lsy[0]) + 2*shifty
            self.img.setRect(QRectF(left, bottom, width, height))


class OWHyper(OWWidget):
    name = "Hyperspectra"
    inputs = [("Data", Orange.data.Table, 'set_data', Default),
              ("Data subset", Orange.data.Table, 'set_subset', Default)]
    outputs = [("Selection", Orange.data.Table)]
    icon = "icons/unknown.svg"

    settingsHandler = DomainContextHandler()

    imageplot = SettingProvider(ImagePlot)
    curveplot = SettingProvider(CurvePlot)

    integration_method = Setting(0)
    integration_methods = [Integrate.Simple, Integrate.Baseline,
                           Integrate.PeakMax, Integrate.PeakBaseline]

    lowlim = Setting(None)
    highlim = Setting(None)

    class Warning(OWWidget.Warning):
        threshold_error = Msg("Low slider should be less than High")

    def __init__(self):
        super().__init__()

        dbox = gui.widgetBox(self.controlArea, "Integration")

        rbox = gui.radioButtons(
            dbox, self, "integration_method", callback=self._change_integration)
        gui.appendRadioButton(rbox, "Integrate from 0")
        gui.appendRadioButton(rbox, "Integrate from baseline")
        gui.appendRadioButton(rbox, "Peak from 0")
        gui.appendRadioButton(rbox, "Peak from baseline")

        gui.rubber(self.controlArea)

        splitter = QSplitter(self)
        splitter.setOrientation(Qt.Vertical)
        self.imageplot = ImagePlot(self)
        self.curveplot = CurvePlot(self, select=SELECTONE)
        splitter.addWidget(self.imageplot)
        splitter.addWidget(self.curveplot)
        self.mainArea.layout().addWidget(splitter)

        self.line1 = MovableVlineWD(position=self.lowlim, label="", setvalfn=self.set_lowlim,
                                    confirmfn=self.edited, report=self.curveplot)
        self.line2 = MovableVlineWD(position=self.highlim, label="", setvalfn=self.set_highlim,
                                    confirmfn=self.edited, report=self.curveplot)
        self.curveplot.add_marking(self.line1)
        self.curveplot.add_marking(self.line2)

        self.resize(900, 700)
        self.graph_name = "imageplot.plotview"

    def edited(self):
        self.imageplot.set_integral_limits()

    def set_lowlim(self, v):
        self.lowlim = v

    def set_highlim(self, v):
        self.highlim = v

    def selection_changed(self):
        self.imageplot.set_integral_limits()

    def _change_integration(self):
        self.imageplot.set_integral_limits()

    def set_data(self, data):
        self.closeContext()
        self.curveplot.set_data(data)
        if self.curveplot.data_x is not None:
            minx = self.curveplot.data_x[0]
            maxx = self.curveplot.data_x[-1]
            if self.lowlim is None or not minx <= self.lowlim <= maxx:
                self.lowlim = min(self.curveplot.data_x)
                self.line1.setValue(self.lowlim)
            if self.highlim is None or not minx <= self.highlim <= maxx:
                self.highlim = max(self.curveplot.data_x)
                self.line2.setValue(self.highlim)
        self.imageplot.set_data(data)
        self.openContext(data)

    def set_subset(self, data):
        pass


def main(argv=None):
    if argv is None:
        argv = sys.argv
    argv = list(argv)
    app = QApplication(argv)
    w = OWHyper()
    w.show()
    #data = Orange.data.Table("whitelight.gsf")
    data = Orange.data.Table("/home/marko/dust/20160831_06_Paris_25x_highmag.hdr")
    w.set_data(data)
    w.handleNewSignals()
    rval = app.exec_()
    w.set_data(None)
    w.handleNewSignals()
    w.deleteLater()
    del w
    app.processEvents()
    gc.collect()
    return rval

if __name__ == "__main__":
    sys.exit(main())
