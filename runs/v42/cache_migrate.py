"""Copy structurally verified content-addressed tasks; never alter stage signatures."""
from pathlib import Path
import argparse,json,shutil,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.config import load_config
from materflow.schema import read_json,write_json,stable_id,file_hash
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--config',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    c=load_config(a.config);source=Path(a.source).resolve();dest=c['output_dir']
    if source==dest:raise ValueError('Migration requires a new output directory')
    stats={'format_version':2,'apply':a.apply,'source':str(source),'destination':str(dest),'verified':0,'copied':0,'legacy_ocr_unverified':0,'invalid':0,
           'runtime_key_check_required':True,'old_inputs_modified':False}
    audit=dest/'migration.jsonl';dest.mkdir(parents=True,exist_ok=True)
    with audit.open('a',encoding='utf-8') as log:
        for kind in ('detection','semantic','ocr'):
            for path in (source/'internal/cache'/kind).glob('*.json'):
                reason=None
                try:
                    item=read_json(path);key=item.get('request_id',item.get('key'))
                    if kind=='ocr' and not str(key).startswith('OCR2'):
                        stats['legacy_ocr_unverified']+=1;continue
                    payload=item['result'] if kind=='ocr' else item['raw']
                    if key!=path.stem or item.get('digest')!=stable_id('',payload):raise ValueError('content digest mismatch')
                    stats['verified']+=1
                    target=dest/'internal/cache'/kind/path.name
                    if a.apply:
                        target.parent.mkdir(parents=True,exist_ok=True)
                        if target.exists() and file_hash(target)!=file_hash(path):raise ValueError('Existing different destination preserved')
                        if not target.exists():shutil.copy2(path,target);stats['copied']+=1
                except (ValueError,KeyError,OSError) as error:
                    reason=str(error);stats['invalid']+=1
                log.write(json.dumps({'path':str(path),'sha256':file_hash(path),'error':reason},ensure_ascii=False)+'\n')
    write_json(dest/'migration_summary.json',stats);print(json.dumps(stats,ensure_ascii=False))
if __name__=='__main__':main()
