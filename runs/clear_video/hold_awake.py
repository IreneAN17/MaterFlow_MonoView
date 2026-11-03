"""Hold a temporary Windows execution request only while this pipeline process lives."""
import ctypes,json,os,sys
from ctypes import wintypes
from datetime import datetime,timezone
from pathlib import Path
k=ctypes.WinDLL("kernel32",use_last_error=True)
k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
k.OpenProcess.restype=wintypes.HANDLE
k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
k.WaitForSingleObject.restype=wintypes.DWORD
k.SetThreadExecutionState.argtypes=[wintypes.DWORD]
k.SetThreadExecutionState.restype=wintypes.DWORD
k.CloseHandle.argtypes=[wintypes.HANDLE]
target=int(sys.argv[1]);handle=k.OpenProcess(0x100000,False,target)
if not handle:raise ctypes.WinError(ctypes.get_last_error())
status=Path(__file__).resolve().parents[2]/"output/clear_video/diagnostics/keep_awake.json"
def record(active):
    status.write_text(json.dumps({"active":active,"helper_pid":os.getpid(),"pipeline_parent_pid":target,
        "request":"temporary ES_SYSTEM_REQUIRED; no power-plan or GPU-clock changes; display may sleep",
        "updated_utc":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf8")
try:
    if not k.SetThreadExecutionState(0x80000001):raise ctypes.WinError(ctypes.get_last_error())
    record(True);print("Temporary system-awake request active",flush=True)
    while k.WaitForSingleObject(handle,45000)==258:pass
finally:
    k.SetThreadExecutionState(0x80000000);k.CloseHandle(handle);record(False)
    print("Temporary system-awake request released",flush=True)
