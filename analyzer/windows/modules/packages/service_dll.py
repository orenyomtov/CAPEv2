import ctypes
import logging
import sys
from winreg import HKEY_LOCAL_MACHINE, KEY_ALL_ACCESS, REG_EXPAND_SZ, REG_MULTI_SZ, REG_SZ, CloseKey, CreateKeyEx, SetValueEx

from lib.api.process import Process
from lib.common.abstracts import Package
from lib.common.common import check_file_extension
from lib.common.defines import ADVAPI32, KERNEL32

INJECT_CREATEREMOTETHREAD = 0
INJECT_QUEUEUSERAPC = 1
SC_MANAGER_CONNECT = 0x0001
SC_MANAGER_CREATE_SERVICE = 0x0002
SC_MANAGER_ENUMERATE_SERVICE = 0x0004
SC_MANAGER_LOCK = 0x0008
SC_MANAGER_QUERY_LOCK_STATUS = 0x0010
SC_MANAGER_MODIFY_BOOT_CONFIG = 0x0020
SC_MANAGER_ALL_ACCESS = (
    SC_MANAGER_CONNECT
    | SC_MANAGER_CREATE_SERVICE
    | SC_MANAGER_ENUMERATE_SERVICE
    | SC_MANAGER_LOCK
    | SC_MANAGER_QUERY_LOCK_STATUS
    | SC_MANAGER_MODIFY_BOOT_CONFIG
)
SERVICE_QUERY_CONFIG = 0x0001
SERVICE_CHANGE_CONFIG = 0x0002
SERVICE_QUERY_STATUS = 0x0004
SERVICE_ENUMERATE_DEPENDENTS = 0x0008
SERVICE_START = 0x0010
SERVICE_STOP = 0x0020
SERVICE_PAUSE_CONTINUE = 0x0040
SERVICE_INTERROGATE = 0x0080
SERVICE_USER_DEFINED_CONTROL = 0x0100
SERVICE_ALL_ACCESS = (
    SERVICE_QUERY_CONFIG
    | SERVICE_CHANGE_CONFIG
    | SERVICE_QUERY_STATUS
    | SERVICE_ENUMERATE_DEPENDENTS
    | SERVICE_START
    | SERVICE_STOP
    | SERVICE_PAUSE_CONTINUE
    | SERVICE_INTERROGATE
    | SERVICE_USER_DEFINED_CONTROL
)
SERVICE_WIN32_OWN_PROCESS = 0x0010
SERVICE_WIN32_SHARE_PROCESS = 0x0020
SERVICE_INTERACTIVE_PROCESS = 0x0100
SERVICE_DEMAND_START = 0x0003
SERVICE_ERROR_IGNORE = 0x0000
log = logging.getLogger(__name__)


class ServiceDll(Package):
    """Service Dll analysis package."""

    def __init__(self, options=None, config=None):
        if options is None:
            options = {}
        self.config = config
        self.options = options
        self.options["curdir"] = "%SystemRoot%\system32"

    PATHS = [
        ("SystemRoot", "system32", "sc.exe"),
    ]

    def set_keys(self, servicename, dllpath):

        svchost_path = r"Software\Microsoft\Windows NT\CurrentVersion\Svchost"
        parameter_path = rf"System\CurrentControlSet\Services\{servicename}\Parameters"

        try:
            log.info("Adding Parameters value: %s -> ServiceDll = %s", parameter_path, dllpath)
            with CreateKeyEx(HKEY_LOCAL_MACHINE, parameter_path, 0, KEY_ALL_ACCESS) as key:
                SetValueEx(key, "ServiceDll", 0, REG_EXPAND_SZ, dllpath)
                CloseKey(key)
        except Exception as e:
            log.info("Error setting registry value: %s", e)
            # Service is not installed
            return

        try:
            log.info("Adding capegroup value: capegroup = %s", servicename)
            with CreateKeyEx(HKEY_LOCAL_MACHINE, svchost_path, 0, KEY_ALL_ACCESS) as key:
                SetValueEx(key, "capegroup", 0, REG_MULTI_SZ, [servicename])
                CloseKey(key)
        except Exception as e:
            log.info("Error setting registry value: %s", e)
            return

    def start(self, path):
        try:
            servicename = self.options.get("servicename", "CAPEService").encode("utf8")
            if servicename == "blank":
                servicename = " ".encode("utf8")
            servicedesc = self.options.get("servicedesc", "CAPE Service").encode("utf8")
            arguments = self.options.get("arguments")
            path = check_file_extension(path, ".dll")
            svcpath = r"%SystemRoot%\system32\svchost.exe"
            binpath = f"{svcpath} -k capegroup".encode("utf8")
            dllpath = f"{path}"
            if arguments:
                dllpath += f" {arguments}"
            scm_handle = ADVAPI32.OpenSCManagerA(None, None, SC_MANAGER_ALL_ACCESS)
            if scm_handle == 0:
                log.info("Failed to open SCManager")
                log.info(ctypes.FormatError())
                return
            service_handle = ADVAPI32.CreateServiceA(
                scm_handle,
                servicename,
                servicedesc,
                SERVICE_ALL_ACCESS,
                SERVICE_WIN32_SHARE_PROCESS,
                SERVICE_DEMAND_START,
                SERVICE_ERROR_IGNORE,
                binpath,
                None,
                None,
                None,
                None,
                None,
            )
            if service_handle == 0:
                log.info("Failed to create service")
                log.info(ctypes.FormatError())
                return
            log.info("Created service %s (handle: 0x%s)", servicename.decode(), service_handle)
            self.set_keys(servicename.decode(), dllpath)
            servproc = Process(options=self.options, config=self.config, pid=self.config.services_pid)
            filepath = servproc.get_filepath()
            servproc.inject(injectmode=INJECT_QUEUEUSERAPC, interest=filepath, nosleepskip=True)
            servproc.close()
            KERNEL32.Sleep(500)
            service_launched = ADVAPI32.StartServiceA(service_handle, 0, None)
            if service_launched:
                log.info("Successfully started service")
            else:
                log.info(ctypes.FormatError())
                log.info("Failed to start service")
            ADVAPI32.CloseServiceHandle(service_handle)
            ADVAPI32.CloseServiceHandle(scm_handle)
            return
        except Exception as e:
            log.info(sys.exc_info()[0])
            log.info(e)
            log.info(e.__dict__)
            log.info(e.__class__)
            log.exception(e)
