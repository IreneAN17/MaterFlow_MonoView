"""Thread-owned temporary Windows execution state, never a power-plan change."""
from contextlib import contextmanager
import threading,sys
_local=threading.local()
@contextmanager
def keep_awake():
    depth=getattr(_local,'depth',0);_local.depth=depth+1
    kernel=None;state={'platform':sys.platform,'requested':False,'acquired':False}
    try:
        if sys.platform=='win32' and not depth:
            import ctypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            state.update(requested=True,acquired=bool(kernel.SetThreadExecutionState(0x80000001)))
        yield state
    finally:
        _local.depth=depth
        if kernel is not None and state['acquired']:
            kernel.SetThreadExecutionState(0x80000000)
