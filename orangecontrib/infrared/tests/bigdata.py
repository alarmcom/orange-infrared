import os

import serverfiles
from Orange.misc.environ import data_dir

import orangecontrib.infrared  # loads file readers


server = serverfiles.ServerFiles("http://193.2.72.57/infrared-data/")
localfiles = serverfiles.LocalFiles(
    os.path.join(data_dir(), "orange-infrared"), serverfiles=server)


def spectra20nea():
    return localfiles.localpath_download("spectra20.nea")
