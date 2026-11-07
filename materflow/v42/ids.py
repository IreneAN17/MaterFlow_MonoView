"""Transactional allocations, compatible legacy snapshot exported at a stage boundary."""
import sqlite3
from ..v41.runtime import Ids as LegacyIds
from ..schema import write_json
class Ids(LegacyIds):
    def __init__(self,path,run_id):
        super().__init__(path,run_id)
        self.db=sqlite3.connect(str(self.path)+'.sqlite')
        self.db.executescript('CREATE TABLE IF NOT EXISTS meta(run_id TEXT);CREATE TABLE IF NOT EXISTS allocations(kind TEXT,uid TEXT,public TEXT UNIQUE,PRIMARY KEY(kind,uid));CREATE TABLE IF NOT EXISTS counters(kind TEXT PRIMARY KEY,n INTEGER);')
        row=self.db.execute('SELECT run_id FROM meta').fetchone()
        if row and row[0]!=run_id:raise ValueError('ID database run mismatch')
        with self.db:
            if not row:self.db.execute('INSERT INTO meta VALUES(?)',(run_id,))
            for kind,mapping in self.data['maps'].items():
                for uid,public in mapping.items():
                    old=self.db.execute('SELECT public FROM allocations WHERE kind=? AND uid=?',(kind,uid)).fetchone()
                    if old and old[0]!=public:raise ValueError('Published ID conflicts with database')
                    self.db.execute('INSERT OR IGNORE INTO allocations VALUES(?,?,?)',(kind,uid,public))
            for kind,uid,public in self.db.execute('SELECT * FROM allocations'):
                self.data['maps'].setdefault(kind,{})[uid]=public
            for kind,mapping in self.data['maps'].items():
                n=max((int(v.rsplit('_',1)[-1]) if '_' in v else int(v[len(kind):]) for v in mapping.values()),default=0)
                self.db.execute('INSERT INTO counters VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET n=MAX(n,excluded.n)',(kind,n))
    def get(self,kind,uid):
        with self.db:
            row=self.db.execute('SELECT public FROM allocations WHERE kind=? AND uid=?',(kind,uid)).fetchone()
            if row:public=row[0]
            else:
                self.db.execute('INSERT OR IGNORE INTO counters VALUES(?,0)',(kind,))
                self.db.execute('UPDATE counters SET n=n+1 WHERE kind=?',(kind,))
                n=self.db.execute('SELECT n FROM counters WHERE kind=?',(kind,)).fetchone()[0]
                public=f'{kind}{n:03d}' if kind in ('O','E','M','S','L') else f'{kind}_{n}'
                self.db.execute('INSERT INTO allocations VALUES(?,?,?)',(kind,uid,public))
        self.data['maps'].setdefault(kind,{})[uid]=public
        self.data.setdefault('retired',{}).setdefault(kind,{}).pop(uid,None)
        self.data.setdefault('redirects',{}).pop(public,None)
        return public
    def save(self):
        # Compatibility methods call save per allocation; SQLite is authoritative.
        pass
    def flush(self):
        write_json(self.path,self.data)
        self.db.close()
