"""Single persistent isolated CPU worker, one bounded in-flight request."""
import json,subprocess,threading,queue,time,os
from collections import deque
from ..schema import write_json
class OCRWorkerClient:
    def __init__(self,config):
        self.c=config;self.process=None;self.starts=0;self.calls=0;self.startup_s=0;self.engine_init_s=0;self.manifest_s=0;self.restarts=0;self.errors=deque(maxlen=40)
    def start(self):
        if self.process is not None and self.process.poll() is None:return
        started=time.perf_counter()
        environment=os.environ.copy();environment.update(PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
        self.process=subprocess.Popen([str(self.c['ocr_python']),'-u',str(self.c['repo_root']/'runs/v42/ocr_worker.py'),'--serve'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1,env=environment)
        self.messages=queue.Queue(maxsize=2)
        process=self.process;messages=self.messages
        def readout():
            try:
                for line in process.stdout:messages.put(line)
            except (OSError,UnicodeError) as error:self.errors.append("Protocol read failed: "+str(error))
            finally:messages.put(None)
        def readerr():
            for line in process.stderr:self.errors.append(line[-1000:])
        threading.Thread(target=readout,daemon=True).start();threading.Thread(target=readerr,daemon=True).start()
        self.starts+=1
        ready=self.receive(120)
        if ready.get('type')!='ready':raise RuntimeError('OCR ready protocol error')
        if hasattr(self,'manifest') and self.manifest!=ready['manifest']:
            self.close();raise RuntimeError('OCR model/provider manifest changed during recovery')
        self.manifest=ready['manifest'];self.startup_s+=time.perf_counter()-started
        self.engine_init_s+=ready.get('engine_init_s',0);self.manifest_s+=ready.get('model_manifest_s',0)
    def receive(self,timeout):
        try:line=self.messages.get(timeout=timeout)
        except queue.Empty:raise RuntimeError('OCR worker result timeout; '+''.join(self.errors))
        if line is None:raise RuntimeError('OCR worker exited; '+''.join(self.errors))
        try:return json.loads(line)
        except ValueError as e:raise RuntimeError('OCR protocol invalid: '+line[:300]) from e
    def iter_results(self,requests,mode):
        self.start()
        for item in requests:
            # Only one path-referenced ROI is in flight, no unbounded image queue.
            message=json.dumps({'item':item,'mode':mode},ensure_ascii=False)
            if len(message.encode('utf-8'))>1048576:raise ValueError('OCR request metadata exceeds 1MB')
            for retry in range(2):
                try:
                    self.start()
                    self.process.stdin.write(message+'\n');self.process.stdin.flush()
                    result=self.receive(300);self.calls+=1
                    break
                except (RuntimeError,OSError) as error:
                    self.close()
                    if retry:raise
                    self.restarts+=1;self.errors.append('Restarting uncommitted request: '+str(error))
            if result.get('type')!='result' or result.get('request_id')!=item.get('request_id'):
                raise RuntimeError('OCR request/result identity mismatch')
            if result.get('error'):raise RuntimeError('OCR input/inference failed: '+result['error'])
            yield result['result']
    def close(self):
        if self.process is None:return
        try:
            if self.process.poll() is None:
                self.process.stdin.close();self.process.wait(timeout=10)
        except (OSError,subprocess.TimeoutExpired):
            self.process.terminate();self.process.wait(timeout=10)
        finally:
            for stream in (self.process.stdin,self.process.stdout,self.process.stderr):stream.close()
            self.process=None
def client(context):
    if '_ocr_worker' not in context:context['_ocr_worker']=OCRWorkerClient(context['config'])
    value=context['_ocr_worker'];value.start();return value
