"""Windows-owned CLI trees, admitted to a kill-on-close Job at creation.

The job is unnamed and non-inheritable, and is absent from the explicit child
handle list. There is no create-then-assign interval and no unprotected fallback.
This is a lifetime boundary for ordinary CreateProcess descendants, not a
security boundary against hostile same-user code that uses an external broker.

Win32 contracts:
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import shutil
import subprocess
import sys
import threading
import time


OWNERSHIP_MECHANISM = "windows_job_v1"
_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_LIST = 0x0002000D
_HANDLE_LIST = 0x00020002
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_INFINITE = 0xFFFFFFFF


if os.name == "nt":
    import msvcrt

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _SIZE_T = ctypes.c_size_t

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", _SIZE_T),
                    ("MaximumWorkingSetSize", _SIZE_T),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", _SIZE_T),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", _SIZE_T), ("JobMemoryLimit", _SIZE_T),
                    ("PeakProcessMemoryUsed", _SIZE_T), ("PeakJobMemoryUsed", _SIZE_T)]

    class _StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                    ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE)]

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    _kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
    _kernel.SetInformationJobObject.restype = wintypes.BOOL
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                         wintypes.DWORD, ctypes.POINTER(_SIZE_T)]
    _kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, _SIZE_T,
                                                  ctypes.c_void_p, _SIZE_T, ctypes.c_void_p,
                                                  ctypes.c_void_p]
    _kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _kernel.DeleteProcThreadAttributeList.restype = None
    _kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                      ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.LPCWSTR,
                                      ctypes.POINTER(_StartupInfoEx),
                                      ctypes.POINTER(_ProcessInformation)]
    _kernel.CreateProcessW.restype = wintypes.BOOL
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel.GetCurrentProcess.argtypes = []
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                        wintypes.BOOL, wintypes.DWORD]
    _kernel.DuplicateHandle.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt" or sys.getwindowsversion().major < 10:
        raise OSError("Owned CLI execution requires Windows 10 or newer; unprotected launch is disabled.")


def _winerror() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def _close_handle(handle: int | None) -> None:
    if handle and not _kernel.CloseHandle(handle):
        raise _winerror()


def _new_job() -> int:
    # NULL security attributes make the sole job handle non-inheritable.
    handle = _kernel.CreateJobObjectW(None, None)
    if not handle:
        raise _winerror()
    limits = _ExtendedLimits()
    # Neither BREAKAWAY_OK nor SILENT_BREAKAWAY_OK is allowed.
    limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not _kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = _winerror()
        _close_handle(handle)
        raise error
    return handle


def _duplicate_handle(handle: int, *, inherit: bool = False) -> int:
    result = wintypes.HANDLE()
    owner = _kernel.GetCurrentProcess()
    if not _kernel.DuplicateHandle(owner, handle, owner, ctypes.byref(result), 0, inherit, 2):
        raise _winerror()
    return result.value


def _create_process(job: int, args: list[str], cwd, env, handles: list[int]) -> tuple[int, int]:
    """Assign the Job in CreateProcess itself, before user code can execute."""
    size = _SIZE_T()
    _kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    if not size.value:
        raise _winerror()
    buffer = ctypes.create_string_buffer(size.value)
    if not _kernel.InitializeProcThreadAttributeList(buffer, 2, 0, ctypes.byref(size)):
        raise _winerror()
    try:
        jobs = (wintypes.HANDLE * 1)(job)
        inherited = (wintypes.HANDLE * len(handles))(*handles)
        for attribute, values in ((_JOB_LIST, jobs), (_HANDLE_LIST, inherited)):
            if not _kernel.UpdateProcThreadAttribute(buffer, 0, attribute, values,
                                                      ctypes.sizeof(values), None, None):
                raise _winerror()
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
        startup.lpAttributeList = ctypes.cast(buffer, ctypes.c_void_p)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        environment = None
        if env is not None:
            if any(not isinstance(k, str) or not isinstance(v, str) or not k or "=" in k
                   or "\0" in k or "\0" in v for k, v in env.items()):
                raise ValueError("Invalid owned process environment.")
            environment = ctypes.create_unicode_buffer("\0".join(
                f"{key}={value}" for key, value in sorted(env.items(), key=lambda pair: pair[0].upper())) + "\0\0")
        information = _ProcessInformation()
        flags = 0x00080000 | 0x00000400 | 0x08000000 | 0x00000200
        # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT |
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP. No breakaway flag.
        if not _kernel.CreateProcessW(args[0], command, None, None, True, flags,
                                       environment, os.fspath(cwd) if cwd is not None else None,
                                       ctypes.byref(startup), ctypes.byref(information)):
            raise _winerror()
        try:
            _close_handle(information.hThread)
        except BaseException:
            # Caller still owns the kill-on-close job, so its failure cleanup
            # terminates the child even if releasing this thread handle failed.
            _close_handle(information.hProcess)
            raise
        return information.dwProcessId, information.hProcess
    finally:
        _kernel.DeleteProcThreadAttributeList(buffer)


class OwnedProcess:
    """The small binary Popen surface used by the research runtime."""

    def __init__(self, args, pid, process, job, stdin, stdout, stderr):
        self.args, self.pid, self.returncode = args, pid, None
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self._process_handle, self._job_handle = process, job
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._closed = False

    def _release_job(self):
        # Call with self._lock held. Closing also kills descendants after the
        # direct CLI has already exited; checking only its PID is insufficient.
        if self._job_handle is not None:
            _close_handle(self._job_handle)
            self._job_handle = None

    def poll(self):
        with self._lock:
            if self.returncode is not None or self._process_handle is None:
                return self.returncode
            result = _kernel.WaitForSingleObject(self._process_handle, 0)
            if result == _WAIT_TIMEOUT:
                return None
            if result != _WAIT_OBJECT_0:
                raise _winerror()
            code = wintypes.DWORD()
            if not _kernel.GetExitCodeProcess(self._process_handle, ctypes.byref(code)):
                raise _winerror()
            self.returncode = code.value
            self._release_job()
            return self.returncode

    def wait(self, timeout=None):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            # A duplicate stays valid if another thread concurrently closes the
            # wrapper. Do not hold the state lock over a blocking kernel wait.
            handle = _duplicate_handle(self._process_handle)
        try:
            milliseconds = _INFINITE if timeout is None else min(0xFFFFFFFE, max(0, int(timeout * 1000 + .999)))
            result = _kernel.WaitForSingleObject(handle, milliseconds)
            if result == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if result != _WAIT_OBJECT_0:
                raise _winerror()
            with self._lock:
                if self.returncode is None:
                    code = wintypes.DWORD()
                    if not _kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                        raise _winerror()
                    self.returncode = code.value
                self._release_job()
                return self.returncode
        finally:
            _close_handle(handle)

    def kill(self):
        with self._lock:
            self._release_job()

    terminate = kill

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self.kill()
            self.wait(timeout=5)
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        # Broken stdin after termination must not skip closure
                        # of the remaining streams and the process handle.
                        pass
            with self._lock:
                if self._process_handle is not None:
                    _close_handle(self._process_handle)
                    self._process_handle = None
            self._closed = True

    def __del__(self):
        # No atexit is required for the guarantee: the kernel closes this
        # process's sole job handle even for os._exit or TerminateProcess.
        try:
            with self._lock:
                self._release_job()
                if self._process_handle is not None:
                    _close_handle(self._process_handle)
                    self._process_handle = None
        except Exception:
            pass


def spawn_owned(args, *, cwd=None, env=None, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1) -> OwnedProcess:
    """Launch without a shell, or fail before any unowned child can execute.

    Only PIPE/DEVNULL standard streams are supported by this private runtime
    API. Parent pipe descriptors stay non-inheritable; only child-side duplicate
    handles enter the explicit HANDLE_LIST. The Job handle never does.
    """
    _require_windows()
    if isinstance(args, (str, bytes)) or not args:
        raise ValueError("Owned process command must be a nonempty argument list.")
    args = [os.fspath(value) for value in args]
    if any(not isinstance(value, str) or "\0" in value for value in args):
        raise ValueError("Owned process arguments must be strings without NUL.")
    executable = shutil.which(args[0])
    if not executable or os.path.splitext(executable)[1].lower() in {".cmd", ".bat", ".ps1"}:
        raise OSError("Owned process requires an existing native executable; shell launchers are disabled.")
    args[0] = os.path.abspath(executable)
    if any(stream not in (subprocess.PIPE, subprocess.DEVNULL) for stream in (stdin, stdout, stderr)):
        raise ValueError("Owned process streams must use PIPE or DEVNULL.")
    job = _new_job()
    child_handles, descriptors, streams = [], [], []
    process = None
    try:
        for number, mode in enumerate((stdin, stdout, stderr)):
            if mode == subprocess.PIPE:
                read_fd, write_fd = os.pipe()
                descriptors.extend((read_fd, write_fd))
                parent_fd, child_fd = (write_fd, read_fd) if number == 0 else (read_fd, write_fd)
                child_handles.append(_duplicate_handle(msvcrt.get_osfhandle(child_fd), inherit=True))
                os.close(child_fd)
                descriptors.remove(child_fd)
                stream = os.fdopen(parent_fd, "wb" if number == 0 else "rb", buffering=bufsize)
                descriptors.remove(parent_fd)
                streams.append(stream)
            else:
                fd = os.open(os.devnull, os.O_RDONLY if number == 0 else os.O_WRONLY)
                descriptors.append(fd)
                child_handles.append(_duplicate_handle(msvcrt.get_osfhandle(fd), inherit=True))
                os.close(fd)
                descriptors.remove(fd)
                streams.append(None)
        pid, process = _create_process(job, args, cwd, env, child_handles)
        # Finish fallible temporary-handle cleanup before transferring the Job.
        # A failed launch must not strand ownership in an unreturned wrapper
        # retained by an exception traceback.
        for handle in list(child_handles):
            _close_handle(handle)
            child_handles.remove(handle)
        result = OwnedProcess(args, pid, process, job, *streams)
        process, job = None, None  # ownership transferred only after construction
        return result
    finally:
        cleanup_errors = []

        def cleanup(operation, value):
            try:
                operation(value)
            except OSError as exc:
                cleanup_errors.append(exc)

        if job is not None:
            cleanup(_close_handle, job)
        for handle in child_handles:
            cleanup(_close_handle, handle)
        for fd in descriptors:
            cleanup(os.close, fd)
        if job is not None:
            for stream in streams:
                if stream is not None:
                    cleanup(lambda value: value.close(), stream)
        if process is not None:
            cleanup(_close_handle, process)
        if cleanup_errors:
            raise cleanup_errors[0]


def kill_owned(process: OwnedProcess) -> None:
    process.kill()
    process.wait(timeout=5)


def run_owned(args, *, capture_output=True, text=False, encoding=None, errors=None,
              timeout=None, env=None, cwd=None) -> subprocess.CompletedProcess:
    """Bounded-output synchronous probe using the same ownership boundary."""
    if not capture_output:
        raise ValueError("Owned probes require captured output.")
    process = spawn_owned(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, bufsize=0)
    output = [bytearray(), bytearray()]
    failure = []
    output_lock = threading.Lock()

    def drain(stream, index):
        try:
            while chunk := stream.read(8192):
                with output_lock:
                    if len(output[index]) + len(chunk) > 1_000_000:
                        failure.append(OSError("CLI authentication status exceeded the 1 MB output limit."))
                        process.kill()
                        return
                    output[index].extend(chunk)
        except (OSError, ValueError) as exc:
            failure.append(exc)

    readers = [threading.Thread(target=drain, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    started = time.monotonic()
    started_readers = []
    try:
        for reader in readers:
            reader.start()
            started_readers.append(reader)
        process.wait(timeout=timeout)
        for reader in readers:
            reader.join(timeout=5 if timeout is None else max(0, timeout - (time.monotonic() - started)))
        if any(reader.is_alive() for reader in readers):
            raise subprocess.TimeoutExpired(args, timeout)
        if failure:
            raise OSError("CLI authentication status could not be read within the output limit.") from failure[0]
        stdout, stderr = map(bytes, output)
        if text or encoding is not None:
            stdout = stdout.decode(encoding or "utf-8", errors or "strict")
            stderr = stderr.decode(encoding or "utf-8", errors or "strict")
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise subprocess.TimeoutExpired(args, timeout, bytes(output[0]), bytes(output[1])) from exc
    finally:
        process.kill()
        process.wait(timeout=5)
        for reader in started_readers:
            reader.join(timeout=5)
        process.close()
