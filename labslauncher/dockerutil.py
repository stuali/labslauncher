"""Miscellaneous utility functions to support labslauncher application."""

import functools
import json
import os
import platform
import traceback

from cachetools import cached, TTLCache
import docker
from PyQt5.QtCore import QTimer
from ratelimitingfilter import RateLimitingFilter
import requests
import semver

import labslauncher
from labslauncher import qtext


@cached(cache=TTLCache(maxsize=1, ttl=300))
def _get_image_meta(image):
    """Retrieve meta data from docker hub for tags of an image.

    :param image: image name.
    """
    tags = list()
    addr = 'https://hub.docker.com/v2/repositories/{}/tags'.format(image)
    while True:
        response = requests.get(addr)
        tags_data = json.loads(response.content.decode())
        tags.extend(tags_data['results'])
        if tags_data['next'] is not None:
            addr = tags_data['next']
        else:
            break
    return tags


def get_image_tags(image, prefix='v'):
    """Retrieve tags from dockerhub of an image.

    :param image: image name, organisation/repository.
    :param prefix: prefix by which to filter images.

    :returns: sorted list of tags, newest first, ordered by semver.
    """
    tags_data = _get_image_meta(image)
    tags = list()
    for t in tags_data:
        name = t['name']
        if name[0] != prefix:
            continue
        try:
            semver.parse(name[1:])
        except ValueError:
            continue
        else:
            tags.append(name[1:])
    ordered_tags = [
        '{}{}'.format(prefix, x) for x in
        sorted(
            tags, reverse=True,
            key=functools.cmp_to_key(semver.compare))]
    return ordered_tags


@functools.lru_cache(5)
def get_image_meta(image, tag):
    """Retrieve meta data from docker hub for a tag.

    :param image: image name.
    :param tag: image tag.

    """
    tags_data = _get_image_meta(image)
    for t in tags_data:
        name = t['name']
        if name == tag:
            return t
    raise IndexError("Tag was not found: \"{}\"".format(tag))


def newest_tag(image, tags=None, client=None):
    """Find the newest available local tag of an image.

    :param tag: list of tags, if None dockerhub is queried.
    :param client: a docker client.
    """
    if client is None:
        client = docker.from_env()
    if tags is None:
        tags = get_image_tags(image)

    latest = None
    for tag in tags:
        try:
            client.images.get("{}:{}".format(image, tag))
        except docker.errors.ImageNotFound:
            pass
        else:
            latest = tag
            break
    return latest


def pull_with_progress(image, tag):
    """Pull an image, yielding download progress.

    :param image: image name.
    :param tag: image tag.

    :yields: downloaded bytes, total bytes.

    """
    if platform.system() == "Darwin":
        path = "/Applications/Docker.app/Contents/Resources/bin/"
        if path not in os.environ['PATH']:
            os.environ['PATH'] = "{}:{}".format(path, os.environ['PATH'])

    image_tag = get_image_meta(image, tag)
    total = image_tag['full_size']

    # to get feedback we need to use the low-level API
    client = docker.APIClient()

    layers = dict()
    pull_log = client.pull(image, tag=tag, stream=True)
    for response in (x.decode() for x in pull_log):
        for line in response.splitlines():
            resp = json.loads(line)
            if "status" in resp and resp["status"] == "Downloading":
                layers[resp['id']] = resp["progressDetail"]["current"]
                current = sum(layers.values())
                yield current, total


class DockerClient():
    """Handle interaction with docker."""

    status = qtext.Property(('', 'unknown'))
    tag = qtext.StringProperty('')
    _available = qtext.BoolProperty(False)

    def __init__(
            self, image_name, server_name, data_bind, container_cmd,
            host_only, fixed_tag=None, registry='docker.io'):
        """Initialize the client."""
        self.image_name = image_name
        self.server_name = server_name
        self.data_bind = data_bind
        self.container_cmd = container_cmd
        self.host_only = host_only
        self.fixed_tag = fixed_tag
        self.registry = registry
        # TODO: plumb in registry
        self.logger = labslauncher.get_named_logger("DckrClnt")
        # throttle connection errors to once every 5 minutes
        spam = [
            'Could not create docker client',
            'Failed to query docker client']
        self.logger.addFilter(
            RateLimitingFilter(rate=1, per=300, burst=1, match=spam))
        self.logger.info(
           """Creating docker client with options:
           image name: {}
           server name: {}
           data bind: {}
           command: {}
           host only: {}
           fixed tag: {}""".format(
               image_name, server_name, data_bind, container_cmd,
               host_only, fixed_tag))
        self._client = None
        self.total_size = None
        self.final_stats = None
        self.is_running()  # sets up tag, status, and available
        self.heartbeat = QTimer()
        self.heartbeat.setInterval(1000*5)  # 5 seconds
        self.heartbeat.start()
        self.heartbeat.timeout.connect(self.is_running)

    @property
    def docker(self):
        """Return a connected docker client."""
        old_client = self._client
        if self._client is None:
            try:
                self._client = docker.client.DockerClient.from_env()
            except Exception:
                self.logger.exception("Could not create docker client:")
                pass
        try:
            self._client.version()
        except Exception:
            self.logger.exception("Failed to query docker client:")
            self._client = None
            raise ConnectionError("Could not communicate with docker.")
        else:
            if old_client is None:
                self.logger.info("Connection to docker (re)established.")
        return self._client

    def is_running(self):
        """Return whether docker is connected.

        Note if True value does not guaranteed subsequent API calls
        will necessarily succeed.
        """
        try:
            value = self.docker is not None
        except ConnectionError:
            value = False
        if value != self._available.value:
            self._available.value = value
            if value:
                self.tag.value = self.latest_available_tag
                self.set_status()
            else:
                self.tag.value = 'unknown'
                self.set_status('unknown')
        return self._available.value

    @property
    def latest_tag(self):
        """Return the latest tag on dockerhub."""
        if self.fixed_tag is not None:
            return self.fixed_tag
        return get_image_tags(self.image_name)[0]

    @property
    def latest_available_tag(self):
        """Return the latest tag available locally."""
        if self.fixed_tag is not None:
            return self.fixed_tag
        return newest_tag(self.image_name, client=self.docker)

    @property
    def update_available(self):
        """Return whether an updated tag available on dockerhub."""
        if not self._available.value:
            return False
        return self.latest_available_tag != self.latest_tag

    def full_image_name(self, tag=None):
        """Return the image name for the requested tag.

        :param tag: requested tag. If not given the moset recent local
            tag is returned.
        """
        if tag is None:
            tag = self.latest_available_tag
        if tag is None:
            raise ValueError("No local tag.")
        return "{}:{}".format(self.image_name, tag)

    def image(self, tag=None, update=False):
        """Return the docker image.

        :param tag: request specific tag. If not given the most recent local
            image is returned unless `update` is `True`.
        :param update: override `tag` and pull latest image from dockerhub.

        :returns: a docker `Image` or None if request cannot be fulfilled.
        """
        if tag is None:
            tag = self.latest_available_tag
        if update:
            tag = self.latest_tag
        name = self.full_image_name(tag=tag)

        image = None
        try:
            image = self.docker.images.get(name)
        except docker.errors.ImageNotFound:
            if update:
                image = self.pull_image(tag)
        return image

    def pull_image(self, tag=None, progress=None, stopped=None):
        """Pull an image tag whilst updating download progress.

        :param tag: tag to fetch. If None the latest tag is pulled.

        :returns: the image object.

        """
        self.logger.info("Starting pull of image tag: {}.".format(tag))
        if tag is None:
            tag = self.latest_tag
        full_name = self.full_image_name(tag=tag)

        # to get feedback we need to use the low-level API
        self.total_size = None
        for current, total in pull_with_progress(self.image_name, tag):
            if stopped is not None and stopped.is_set():
                return None
            if progress is not None:
                progress.emit(100 * current / total)
            self.total_size = total
        progress.emit(100.0)
        image = self.docker.images.get(full_name)
        self.tag.value = self.latest_available_tag
        self.logger.info("Finished pulling image")
        return image

    @property
    def container(self):
        """Return the server container if one is present, else None."""
        try:
            for cont in self.docker.containers.list(True):
                if cont.name == self.server_name:
                    return cont
        except Exception:
            pass
        return None

    def start_container(self, mount, token, port, aux_port):
        """Start the server container, removing a previous one if necessary.

        .. note:: The behaviour of docker.run is that a pull will be invoked if
            the image is not available locally. To ensure more controlled
            behaviour check .fetch_local_image() first.
        """
        self.logger.info("Starting container.")
        self.clear_container()
        CMD = self.container_cmd.split() + [
            "--NotebookApp.token={}".format(token),
            "--port={}".format(port)]

        try:
            # note: colab requires the port in the container to be equal
            ports = {int(port): int(port), int(aux_port): int(aux_port)}
            if self.host_only:
                ports = {
                    int(port): ('127.0.0.1', int(port)),
                    int(aux_port): ('127.0.0.1', int(aux_port))}
            self.docker.containers.run(
                self.full_image_name(),
                CMD,
                detach=True,
                ports=ports,
                environment=['JUPYTER_ENABLE_LAB=yes'],
                volumes={
                    mount: {
                        'bind': self.data_bind, 'mode': 'rw'}},
                name=self.server_name)
        except Exception:
            self.logger.exception(
                    "Failed to start container.")
            self.last_failure = traceback.format_exc()
            self.last_failure_type = 'unknown'
            win_fs_msg = "Filesharing has been cancelled"
            osx_fs_msg = "Mounts denied"
            if (win_fs_msg in self.last_failure) \
                    or (osx_fs_msg in self.last_failure):
                self.logger.warning("Detected that sharing was disabled.")
                self.last_failure_type = "file_share"
        else:
            self.logger.info("Container started.")
        self.final_stats = None
        self.set_status()

    def clear_container(self, *args):
        """Kill and remove the server container."""
        cont = self.container
        if cont is not None:
            if cont.status == "running":
                self.logger.info("Stopping container.")
                self.final_stats = cont.stats(stream=False)
                cont.kill()
                self.logger.info("Container stopped.")
            self.logger.info("Removing container.")
            cont.remove()
            self.logger.info("Container removed.")
        self.set_status()

    def set_status(self, new=None):
        """Set the container status property."""
        # store the old and the new status
        if self._available.value and new is None:
            c = self.container
            new = "inactive" if c is None else c.status
        self.status.value = (self.status.value[1], new)
