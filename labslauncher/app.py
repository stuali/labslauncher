"""LabsLauncher Application."""

import sys

import docker
from kivy.app import App
from kivy.core.window import Window
from kivy.properties import StringProperty
from kivy.uix.screenmanager import ScreenManager

from labslauncher import LauncherConfig, screens, util

Window.size = (400, 200)


def main():
    """Entry point for application."""
    app = LabsLauncherApp()
    if '--latest' in sys.argv:
        # money patch to run 'latest' container
        app.im_request = 'latest'
        app.set_image()
    app.run()


class LabsLauncherApp(App):
    """LabsLauncher application class."""

    cstatus = StringProperty('unknown')

    def __init__(self, *args, **kwargs):
        """Initialize the application."""
        super().__init__(**kwargs)
        self.docker = docker.from_env()
        self.conf = LauncherConfig()
        self._local_address = "Local address unavailable"

        self.im_tags = util.get_image_tags(self.conf.CONTAINER)
        self.im_request = 'v0.0.1-alpha.1'
        self.set_image()

    def build(self):
        """Build the application."""
        self.sm = ScreenManager()
        self.sm.add_widget(screens.HomeScreen(name='home'))
        self.sm.add_widget(screens.StartScreen(name='start'))

        for screen in ('home', 'start'):
            self.bind(cstatus=self.sm.get_screen(screen).setter('cstatus'))
        self.set_status()
        return self.sm

    @property
    def image_name(self):
        """Return the image name for the requested tag."""
        return "{}:{}".format(self.conf.CONTAINER, self.im_request)

    def get_image(self):
        """Get the docker image."""
        return self.docker.images.get(self.image_name)

    def set_image(self):
        """Set image attribute of this class.

        If the image is not found locally sets `None`.
        """
        try:
            self.image = self.get_image()
        except docker.errors.ImageNotFound:
            self.image = None

    def ensure_image(self):
        """Set image attribute of this class.

        If the image is not found locally the image is pulled.
        """
        try:
            self.image = self.get_image()
        except docker.errors.ImageNotFound:
            self.image = self.pull_tag(self.im_request)

    @property
    def local_address(self):
        """Return server address including port and token parameter."""
        return self._local_address

    def set_local_address(self, port, token):
        """Set the local address attribute of this class."""
        local_address_format = "http://localhost:{}?token={}"
        self._local_address = local_address_format.format(port, token)

    @property
    def container(self):
        """Return the server container if one is present, else None."""
        for cont in self.docker.containers.list(True):
            if cont.name == self.conf.SERVER_NAME:
                return cont
        return None

    def set_status(self):
        """Set the kivy container status property."""
        c = self.container
        new_status = 'inactive'
        if c is not None:
            new_status = c.status
        if new_status != self.cstatus:
            self.cstatus = new_status

    def clear_container(self, *args):
        """Kill and remove the server container."""
        cont = self.container
        if cont is not None:
            if cont.status == "running":
                cont.kill()
            cont.remove()
        self.set_status()

    def start_container(self, mount, token, port, tag=None):
        """Start the server container, removing a previous one if necessary."""
        self.clear_container()
        if tag is None:
            raise NotImplementedError("Calling without tag not supported")

        # colab requires the port in the container to be equal
        CMD = self.conf.CONTAINERCMD + [
            "--NotebookApp.token={}".format(token),
            "--port={}".format(port),
            ]

        try:
            self.docker.containers.run(
                "{}:{}".format(self.conf.CONTAINER, tag),
                CMD,
                detach=True,
                ports={int(port): int(port)},
                environment=['JUPYTER_ENABLE_LAB=yes'],
                volumes={
                    mount: {
                        'bind': self.conf.DATABIND, 'mode': 'rw'}},
                name=self.conf.SERVER_NAME)
            self.set_local_address(port, token)
        except Exception as e:
            # TODO: better feedback on failure
            print(e)
            pass
        self.set_status()

    def pull_tag(self, tag):
        """Pull an image tag."""
        name = "{}:{}".format(self.conf.CONTAINER, tag)
        return self.docker.images.pull(name)
