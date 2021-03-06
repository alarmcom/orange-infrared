import unittest
import numpy as np

import Orange
from Orange.widgets.tests.base import WidgetTest

from orangecontrib.infrared.widgets.owhyper import values_to_linspace, \
    index_values, OWHyper

NAN = float("nan")

class TestReadCoordinates(unittest.TestCase):

    def test_linspace(self):
        v = values_to_linspace(np.array([1, 2, 3]))
        np.testing.assert_equal(np.linspace(*v), [1, 2, 3])
        v = values_to_linspace(np.array([1, 2, 3, float("nan")]))
        np.testing.assert_equal(np.linspace(*v), [1, 2, 3])
        v = values_to_linspace(np.array([1]))
        np.testing.assert_equal(np.linspace(*v), [1])
        v = values_to_linspace(np.array([1.001, 2, 3.002]))
        np.testing.assert_equal(np.linspace(*v), [1.001, 2.0015, 3.002])

    def test_index(self):
        a = np.array([1,2,3])
        v = values_to_linspace(a)
        iv = index_values(a, v)
        np.testing.assert_equal(iv, [0, 1, 2])
        a = np.array([1, 2, 3, 4])
        v = values_to_linspace(a)
        iv = index_values(a, v)
        np.testing.assert_equal(iv, [0, 1, 2, 3])
        a = np.array([1, 2, 3, 6, 5])
        v = values_to_linspace(a)
        iv = index_values(a, v)
        np.testing.assert_equal(iv, [0, 1, 2, 5, 4])


class TestOWCurves(WidgetTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.iris = Orange.data.Table("iris")
        cls.whitelight = Orange.data.Table("whitelight.gsf")
        cls.whitelight_unknown = cls.whitelight.copy()
        cls.whitelight_unknown[0]["value"] = NAN

    def setUp(self):
        self.widget = self.create_widget(OWHyper)

    def test_empty(self):
        self.send_signal("Data", None)

    def test_simple(self):
        self.send_signal("Data", self.whitelight)
        self.send_signal("Data", None)

    def test_unknown(self):
        self.send_signal("Data", self.whitelight)
        levels = self.widget.imageplot.img.levels
        self.send_signal("Data", self.whitelight_unknown)
        levelsu = self.widget.imageplot.img.levels
        np.testing.assert_equal(levelsu, levels)
