"""Application for managing a notebook server."""
import argparse
import functools
import logging
import os
import sys
import traceback

from PyQt5 import sip  # noqa: F401
from PyQt5.QtWidgets import QMessageBox


__version__ = "0.6.0"
__UNCAUGHT__ = "Uncaught exception:"
__LOGDIR__ = os.path.expanduser(os.path.join('~', '.labslauncher'))


def get_named_logger(name):
    """Create a logger with a name."""
    logger = logging.getLogger('{}.{}'.format(__package__, name))
    logger.name = name
    return logger


def log_level():
    """Parser to set logging level."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter, add_help=False)
    modify_log_level = parser.add_mutually_exclusive_group()
    modify_log_level.add_argument(
        '--debug', action='store_const',
        dest='log_level', const=logging.DEBUG, default=logging.INFO,
        help='Verbose logging of debug information.')
    modify_log_level.add_argument(
        '--quiet', action='store_const',
        dest='log_level', const=logging.WARNING, default=logging.INFO,
        help='Minimal logging; warnings only).')
    return parser


def handle_unhandled(logger=None):
    """Set logging of uncaught exceptions and exit application."""
    def _except_hook(logger, orig_hook, exctype, value, tb):
        lines = ''.join(traceback.format_exception(exctype, value, tb))
        if logger is not None:
            logger.critical("{}\n{}".format(__UNCAUGHT__, lines))
        sys.__excepthook__(exctype, value, tb)
        # overriding sys.excepthook confuses pyqt and the program does
        # not die. Let's force that.
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("EPI2ME Labs Launcher error")
        msg.setWindowTitle("Launcher Error")
        msg.setInformativeText(
            "An unexpected error occurred. Please include the log file at:\n\n"
            "{}\n\n"
            "when contacting Oxford Nanopore Technologies support. The "
            "application will now exit.".format(__LOGDIR__))
        msg.setDetailedText(lines)
        msg.exec_()
        sys.exit()
    sys.excepthook = functools.partial(_except_hook, logger, sys.excepthook)


def uncaught_filter(message):
    """Filter uncaught exception messages."""
    filt = int(not message.getMessage().startswith(__UNCAUGHT__))
    return filt


class Defaults(list):
    """A helper class to create configuration data."""

    def __getitem__(self, key):
        """Retrieve list item, or the default value if key is a `str`.

        :param key: int or string.
        """
        if isinstance(key, int):
            return super().__getattr__[key]
        else:
            return self.by_key[key]["default"]
        raise KeyError()

    def get_type(self, key):
        """Return the python type of item.

        :param key: the key of the requested item.
        """
        return self.by_key[key]["type"]

    def get_description(self, key):
        """Return the description od an item.

        :param key: the key of the requested item.
        """
        return self.by_key[key]["desc"]

    def append(self, *values):
        """Append an item."""
        keys = ("title", "desc", "key", "default", "gui_menu")
        data = dict(zip(keys, values))
        data["type"] = type(data["default"])
        data["section"] = self.section
        super().append(data)
        self.by_key[data["key"]] = data

    def __init__(self):
        """Initialize the class."""
        self.section = "epi2melabs-notebook"
        self.by_key = dict()
        self.USE_COLAB = True
        self.append(
            "Registry",
            "The container registry from which to download images.",
            "registry", "docker.io", True)
        self.append(
            "Image",
            "The container image to use from dockerhub.",
            "image_name", "ontresearch/nanolabs-notebook", True)
        self.append(
            "Fixed Tag",
            "Fix the container image to a specific tag.",
            "fixed_tag", "", True)
        self.append(
            "Server Name",
            "The name given to the actively running container.",
            "server_name", "Epi2Me-Labs-Server", True)
        self.append(
            "Data Mount",
            "Location on host computer accessible within notebooks.",
            "data_mount", os.path.expanduser("~"), False)
        self.append(
            "Data bind",
            "Location on server where host mount is accessible.",
            "data_bind", "/epi2melabs/", False)
        self.append(
            "Port",
            "Network port for communication between host and notebook server.",
            "port", 8888, False)
        self.append(
            "Auxiliary Port",
            "Auxiliary network port for additional applications.",
            "aux_port", 8889, False)
        self.append(
            "Security Token",
            "Security token for notebook server.",
            "token", "EPI2MELabs", False)
        self.append(
            "Container command.",
            "Command line arguments to run notebook server.",
            "container_cmd",
            "start-notebook.sh "
            " --NotebookApp.allow_origin='https://colab.research.google.com'"
            " --NotebookApp.disable_check_xsrf=True"
            " --NotebookApp.port_retries=0"
            " --no-browser"
            " --notebook-dir=/", False)
        item = [
            "Colaboratory Homepage",
            "Link displayed for getting started.",
            "colab_link",
            "https://colab.research.google.com/github/epi2me-labs/"
            "resources/blob/master/welcome.ipynb", True]
        if not self.USE_COLAB:
            item[3] = \
                "http://localhost:{port}/lab/tree/epi2me-resources/" \
                + "resources/welcome.ipynb" \
                + "?file-browser-path=/{databind}&token={token}"
        self.append(*item)
        item = [
            "Colaboratory help page",
            "Link to help page on Colaboratory.",
            "colab_help",
            "https://colab.research.google.com/github/epi2me-labs/"
            "resources/blob/master/epi2me-labs-server.ipynb", True]
        if not self.USE_COLAB:
            item[3] = \
                "http://localhost:{port}/lab/tree/epi2me-resources/" \
                + "resources/epi2me-labs-server.ipynb?" \
                + "file-browser-path=/{databind}&token={token}"
        self.append(*item)
        self.append(
            "Docker arguments",
            "Extra arguments to provide to `docker run`.",
            "docker_args", "", True)
        self.append(
            "Local access only",
            "Restrict access to notebook server to this computer only.",
            "docker_restrict", True, True)
        self.append(
            "Send pings",
            "Send usage statistics to ONT.",
            "send_pings", True, False)
