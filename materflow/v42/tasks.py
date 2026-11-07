"""Short SQLite transactions; model calls always run outside transactions."""
import sqlite3,time,json
class TaskLedger:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY,state TEXT,attempt INTEGER,input_ref TEXT,output_ref TEXT,error TEXT,updated REAL)')
        self.db.commit()
    def set(self,key,state,attempt=0,input_ref=None,output_ref=None,error=None):
        with self.db:self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?)',(key,state,attempt,input_ref,output_ref,error,time.time()))
    def close(self):
        if self.db is not None:
            self.db.close();self.db=None
