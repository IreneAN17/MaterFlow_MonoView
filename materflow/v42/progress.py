"""Small heartbeat; complete artifact manifests are written only on stage changes."""
import threading,time
from ..schema import write_json
from ..v41.runtime import utc
class Progress:
    def __init__(self,path,summary,stage):
        self.path,self.summary,self.stage=path,summary,stage
        self.stop=threading.Event();self.lock=threading.Lock()
    def update(self,message=None,**values):
        with self.lock:
            item=self.summary['stages'][self.stage]
            item.update(values,last_heartbeat_utc=utc())
            if message is not None:
                item.update(progress=message,last_progress_utc=utc(),last_completed_task_utc=utc())
                print(message,flush=True)
            small={k:v for k,v in item.items() if k not in ('artifacts','gpu_start','gpu_end')}
            small.update(stage=self.stage,format_version=2)
            write_json(self.path.with_name('status.json'),small)
            if values.get('status') in ('complete','failed','partial','interrupted'):
                write_json(self.path,self.summary)
    def __enter__(self):
        def pulse():
            while not self.stop.wait(5):self.update()
        self.worker=threading.Thread(target=pulse,daemon=True);self.worker.start();return self
    def __exit__(self,*args):
        self.stop.set();self.worker.join()
