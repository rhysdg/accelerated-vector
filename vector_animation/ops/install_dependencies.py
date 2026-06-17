import os
import sys
import site
import subprocess
import ensurepip

from importlib.util import find_spec
from bpy.types import Operator

import bpy


# ── User site-packages helper ──────────────────────────────────────────────
# Blender's snap-bundled Python does not include ~/.local/lib/pythonX.Y/
# in sys.path, so pip --user installs become invisible.  We add it here so
# that *all* inline imports across the addon (serial, websocket, anki_vector)
# can find user-installed packages.

_USER_SITE = None


def _ensure_user_site():
    """Return the user site-packages directory, adding it to ``sys.path``."""
    global _USER_SITE
    if _USER_SITE is None:
        _USER_SITE = site.getusersitepackages()
    if _USER_SITE not in sys.path:
        sys.path.insert(0, _USER_SITE)
        site.addsitedir(_USER_SITE)
    return _USER_SITE


# Ensure visibility immediately so any inline import in the addon works.
_ensure_user_site()


class InstallDependencies(Operator):
    bl_idname = "servo_animation.install_dependencies"
    bl_label = "Install Servo Animation Live Mode Dependencies"
    bl_description = (
        "Install missing live mode dependencies "
        "(requires an active internet connection)"
    )
    bl_options = {'INTERNAL', 'BLOCKING'}

    MODULES = ["serial", "websocket", "anki_vector"]

    python: bpy.props.StringProperty()

    @classmethod
    def poll(cls, _context):
        return not cls.installed()

    @classmethod
    def installed(cls):
        # Ensure user site-packages is on the path so find_spec can see
        # packages installed via pip --user.
        _ensure_user_site()

        for module in cls.MODULES:
            if find_spec(module) is None:
                return False

        return True

    def execute(self, _context):
        try:
            self.install_pip()
            self.install_requirements()
        except (subprocess.CalledProcessError, ImportError) as err:
            self.report({"ERROR"}, str(err))

            return {"CANCELLED"}

        # After a successful install, make sure the freshly-installed
        # packages are visible for the remainder of this session.
        _ensure_user_site()

        return {"FINISHED"}

    def install_pip(self):
        try:
            subprocess.run([self.python, "-m", "pip", "--version"], check=True)
        except subprocess.CalledProcessError:
            ensurepip.bootstrap()
            os.environ.pop("PIP_REQ_TRACKER", None)

    def install_requirements(self):
        file_path = os.path.realpath(__file__)
        dir_path = os.path.dirname(file_path)
        req_file = dir_path + "/../requirements.txt"

        subprocess.run(
            [self.python, "-m", "pip", "install", "--user", "-r", req_file],
            check=True,
        )

    def invoke(self, context, _event):
        try:
            self.python = bpy.app.binary_path_python
        except AttributeError:
            self.python = sys.executable

        return self.execute(context)
