"""Bounded legacy partition migration. Never parses scan_partitions.json."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from materflow.schema import read_json,write_json,file_hash
def migrate(source,output,apply=False):
    source=Path(source).resolve();output=Path(output).resolve()
    if output==source or output.is_relative_to(source):raise ValueError('Migration output must be independent of source')
    output.mkdir(parents=True,exist_ok=True)
    index=output/('partitions.jsonl' if apply else 'dry_run.jsonl')
    if index.exists():raise FileExistsError('Preserve previous migration: '+str(index))
    stats={'format_version':2,'apply':apply,'partitions':0,'max_index_entry_bytes':0,'stage_signatures_modified':False,'original_input_modified':False}
    with index.open('w',encoding='utf-8') as dest:
        for i,meta in enumerate(sorted((source/'internal/scan').glob('*.meta.json'))):
            data=read_json(meta)
            raw=Path(data['path']).resolve()
            if not raw.is_relative_to(source):raise ValueError('Partition path outside selected source run')
            if apply and file_hash(raw)!=data['sha256']:raise ValueError('Partition data digest failed: '+str(raw))
            entry={k:data[k] for k in ('path','source_id','rows','start_s','end_s','sha256','input_signature','logical_counts') if k in data}
            entry.update(format_version=2,legacy_meta_ref=str(meta),legacy_meta_sha256=file_hash(meta),data_digest_verified=apply)
            if apply:
                state=output/'states'/f'{i:06d}.json';reject=output/'rejections'/f'{i:06d}.jsonl'
                write_json(state,data.get('end_state',{}));reject.parent.mkdir(exist_ok=True)
                with reject.open('w',encoding='utf-8') as stream:
                    for row in data.get('rejected',[]):stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
                commit=output/'commits'/f'{i:06d}.json'
                write_json(commit,{'format_version':2,'source_data':str(raw),'source_data_sha256':data['sha256'],
                    'files':{str(p):file_hash(p) for p in (state,reject)}})
                entry.update(state_ref=str(state),reject_ref=str(reject),commit_ref=str(commit),commit_sha256=file_hash(commit))
            line=json.dumps(entry,ensure_ascii=False,separators=(',',':'))
            size=len(line.encode('utf-8'))
            if size>2048:raise ValueError('Index entry exceeds 2KB; inspect rather than truncate facts')
            stats['max_index_entry_bytes']=max(stats['max_index_entry_bytes'],size)
            dest.write(line+'\n');dest.flush();stats['partitions']+=1
    write_json(output/('migration_manifest.json' if apply else 'dry_run_summary.json'),stats)
    return stats
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    print(json.dumps(migrate(a.source,a.output,a.apply)))
if __name__=='__main__':main()
