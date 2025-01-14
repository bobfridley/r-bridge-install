from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

from contextlib import contextmanager
import os
import datetime
import errno
import ctypes.wintypes

try:
    import winreg
except ImportError:
    # py 2
    import _winreg as winreg


@contextmanager
def ignored(*exceptions):
    """Use contextlib to ignore all windows specific errors.
       These are generally encountered from missing registry keys,
       and can safely be ignored in most circumstances."""
    try:
        yield
    except exceptions:
        pass

fnf_exception = getattr(__builtins__,
                        'FileNotFoundError', WindowsError)


def r_path():
    """Find R installation path from registry."""
    r_install_path = None

    # set an epoch for a Windows FILETIME object
    epoch = datetime.datetime(1601, 1, 1)

    root_keys = {
        'HKCU': winreg.HKEY_CURRENT_USER,
        'HKLM': winreg.HKEY_LOCAL_MACHINE
    }
    r_reg_paths = ["SOFTWARE\\R-core\\R",  "SOFTWARE\\R-core\\R64"]

    for (key_name, root_key) in list(root_keys.items()):
        for r_path in r_reg_paths:
            r_reg = None

            try:
                r_reg = winreg.OpenKey(root_key, r_path,
                                       0, (winreg.KEY_READ |
                                           winreg.KEY_WOW64_64KEY))
            except fnf_exception as error:
                if error.errno == errno.ENOENT:
                    pass
                else:
                    raise

            if r_reg:
                try:
                    r_install_path = winreg.QueryValueEx(r_reg, "InstallPath")[0]
                except fnf_exception as error:
                    if error.errno == errno.ENOENT:
                        pass
                    else:
                        raise

                if not r_install_path:
                    """Can't find the install path as a top-level value.
                    Inspect the children keys for versions, and use the most
                    recently installed one as the correct R installation."""
                    max_time = epoch

                    for pos in range(10):
                        # TODO ensure this is robust to errors
                        with ignored(WindowsError):
                            r_base_key = winreg.EnumKey(r_reg, pos)

                            # test for the right path based on age
                            if r_base_key:
                                r_version_key = "{}\\{}".format(
                                    r_path, r_base_key)
                                r_version_reg = winreg.OpenKey(
                                    root_key, r_version_key, 0,
                                    (winreg.KEY_READ |
                                     winreg.KEY_WOW64_64KEY))
                                r_install_path = winreg.QueryValueEx(
                                    r_version_reg, "InstallPath")[0]
                                r_version_info = winreg.QueryInfoKey(r_version_reg)
                                r_install_time = epoch + datetime.timedelta(
                                    microseconds=r_version_info[2]/10)
                                if max_time < r_install_time:
                                    max_time = r_install_time
    return r_install_path

r_install_path = r_path()


def r_version():
    r_version = None
    r_path_l = r_install_path
    if r_path_l is not None:
        r_version = r_path_l.split('-')[1]
    return r_version

r_version_info = r_version()


def r_pkg_path():
    """
    Package path search. Locations searched:
     - HKCU\\Software\\Esri\\ArcGISPro\\RintegrationProPackagePath
     - [USERDOCUMENT]/R/win-library/[3-9].[0-9]/ - default for user R packages
     - [ArcGIS]/Resources/Rintegration/arcgisbinding
    """
    # NOTE to be 100% robust, this may be better implemented as a call to
    #      R, and parsing out the .libPaths() results.
    package_path = None
    package_name = 'arcgisbinding'

    root_key = winreg.HKEY_CURRENT_USER
    reg_path = "SOFTWARE\Esri\ArcGISPro"
    package_key = 'RintegrationProPackagePath'
    pro_reg = None

    try:
        # find the key, 64- or 32-bit we want it all
        pro_reg = winreg.OpenKey(
            root_key, reg_path, 0,
            (winreg.KEY_READ | winreg.KEY_WOW64_64KEY))
    except fnf_exception as error:
        if error.errno == errno.ENOENT:
            pass
        else:
            raise

    if pro_reg:
        try:
            # returns a tuple of (value, type)
            package_path_key = winreg.QueryValueEx(pro_reg, package_key)
            package_path_raw = package_path_key[0]
            if os.path.exists(package_path_raw):
                package_path = package_path_raw
        except fnf_exception as error:
            if error.errno == errno.ENOENT:
                pass
            else:
                raise

    # user's R library in Documents/R/win-library/R-x.x/
    if not package_path and r_version_info is not None:
        # This doesn't automatically work, R_USER, R_LIBS_USER can both override
        # the default location. On my machine, it does't work as it gets the
        # selected location of the Documents library, not the default My Documents
        # path.

        # Call SHGetFolderPath using ctypes.
        CSIDL_PROFILE = 40
        SHGFP_TYPE_CURRENT = 0

        ctypes_buffer = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(
            0, CSIDL_PROFILE, 0, SHGFP_TYPE_CURRENT, ctypes_buffer)
        # NOTE this isn't a language-independent way, but CSIDL_PERSONAL gets
        #      the wrong path.
        documents_folder = os.path.join(ctypes_buffer.value, "Documents")

        # check with R -- what is R_LIBS_USER set to?
        (r_major, r_minor, r_patch) = r_version_info.split(".")

        r_library_package_path = os.path.join(
            documents_folder, "R", "win-library",
            "{}.{}".format(r_major, r_minor), package_name)
        if os.path.exists(r_library_package_path):
            package_path = r_library_package_path

    # R library in ProgramFiles/R-x.xx/library
    if not package_path and r_install_path is not None:
        r_install_package_path = os.path.join(
            r_install_path, "library", package_name)

        if os.path.exists(r_install_package_path):
            package_path = r_install_package_path

    # fallback -- <ArcGIS Install>/Rintegration/arcgisbinding
    if not package_path:
        import arcpy
        arc_install_dir = arcpy.GetInstallInfo()['InstallDir']
        arc_package_dir = os.path.join(
            arc_install_dir, 'Rintegration', package_name)
        if os.path.exists(arc_package_dir):
            package_path = arc_package_dir

    return package_path

r_package_path = r_pkg_path()


def r_pkg_version():
    version = None
    r_package_path = r_pkg_path()
    if r_package_path:
        desc_path = os.path.join(r_package_path, 'DESCRIPTION')
        if os.path.exists(desc_path):
            with open(desc_path) as desc_f:
                for line in desc_f:
                    try:
                        (key, value_raw) = line.strip().split(':')
                    except:
                        # gulp
                        pass
                    if key == 'Version':
                        version = value_raw.strip()
    return version

r_package_version = r_pkg_version()


def r_lib_path():
    """ Package library.  """
    lib_path = None

    # user's R library in Documents/R/win-library/R-x.x/
    if not lib_path and r_version_info is not None:
        # This doesn't automatically work, R_USER, R_LIBS_USER can both override
        # the default location. On my machine, it does't work as it gets the
        # selected location of the Documents library, not the default My Documents
        # path.

        # Call SHGetFolderPath using ctypes.
        CSIDL_PROFILE = 40
        SHGFP_TYPE_CURRENT = 0

        ctypes_buffer = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(
            0, CSIDL_PROFILE, 0, SHGFP_TYPE_CURRENT, ctypes_buffer)
        # NOTE this isn't a language-independent way, but CSIDL_PERSONAL gets
        #      the wrong path.
        documents_folder = os.path.join(ctypes_buffer.value, "Documents")

        # check with R -- what is R_LIBS_USER set to?
        (r_major, r_minor, r_patch) = r_version_info.split(".")

        r_user_library_path = os.path.join(
            documents_folder, "R", "win-library",
            "{}.{}".format(r_major, r_minor))
        if os.path.exists(r_user_library_path):
            lib_path = r_user_library_path

    # R library in ProgramFiles/R-x.xx/library
    if not lib_path and r_install_path is not None:
        r_install_lib_path = os.path.join(
            r_install_path, "library")

        if os.path.exists(r_install_lib_path):
            lib_path = r_install_lib_path

    return lib_path

r_library_path = r_lib_path()

def arcmap_exists(version=None):
    root_key = winreg.HKEY_CURRENT_USER
    reg_path = "SOFTWARE\Esri"
    if not version:
        package_key = "Desktop10.3"
    else:
        package_key = "Desktop{}".format(version)

    arcmap_reg = None
    installed = False
    try:
        # find the key, 64- or 32-bit we want it all
        arcmap_reg = winreg.OpenKey(
            root_key, reg_path, 0,
            (winreg.KEY_READ | winreg.KEY_WOW64_64KEY))
    except fnf_exception as error:
        if error.errno == errno.ENOENT:
            pass
        else:
            raise

    if arcmap_reg:
        installed = True

    return arcmap_reg
