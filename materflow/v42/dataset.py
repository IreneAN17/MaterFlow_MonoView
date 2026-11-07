"""Detection dataset checks. No labels are synthesized and no quality metrics are invented."""
from pathlib import Path
from collections import Counter,defaultdict
import csv
import hashlib
import math
import numpy as np
from PIL import Image
import yaml
from ..schema import file_hash

def validate_dataset(dataset_path,split_manifest_path):
    dataset_path=Path(dataset_path)
    if not dataset_path.is_file():
        return {"status":"awaiting_user_data","errors":["Dataset YAML not supplied"],"images":0}
    definition=yaml.safe_load(dataset_path.read_text(encoding="utf-8-sig"))
    names=definition.get("names",{})
    names=dict(enumerate(names)) if isinstance(names,list) else {int(k):v for k,v in names.items()}
    errors=[];warnings=[];counts=Counter();split_counts=Counter();hashes=defaultdict(list)
    images=[];seen_paths={}
    root=(dataset_path.parent/definition.get("path",".")).resolve()
    if sorted(names)!=list(range(len(names))) or len(set(names.values()))!=len(names):
        errors.append("Class indices must be contiguous and class names unique")
    metadata={}
    if split_manifest_path and Path(split_manifest_path).is_file():
        with Path(split_manifest_path).open(encoding="utf-8-sig",newline="") as stream:
            for row in csv.DictReader(stream):
                key=(root/row["image_path"]).resolve()
                if key in metadata:
                    errors.append("Duplicate split manifest image: "+str(key))
                metadata[key]=row
    else:
        errors.append("Group-aware split_manifest is required")
    groups=defaultdict(set);approx=defaultdict(list)
    for split in ("train","val","test"):
        source=definition.get(split)
        if not source:
            errors.append("Independent split missing: "+split)
            continue
        source=root/source
        if source.is_dir():
            files=sorted(p for p in source.rglob("*") if p.suffix.lower() in (".jpg",".jpeg",".png",".bmp"))
        elif source.is_file():
            files=[(root/line.strip()).resolve() for line in source.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        else:
            errors.append("Split source missing: "+str(source));continue
        if not files:
            errors.append("Empty split: "+split)
        for image in files:
            image=image.resolve()
            if image in seen_paths and seen_paths[image]!=split:
                errors.append("Same image path crosses splits: "+str(image))
            seen_paths[image]=split
            try:
                with Image.open(image) as probe:
                    probe.verify()
                with Image.open(image) as im:
                    w,h=im.size
                    a=np.asarray(im.convert("L").resize((8,8)))
                    phash=bytes(np.packbits(a>=a.mean()))
                digest=file_hash(image)
            except (OSError,ValueError) as e:
                errors.append("Bad image "+str(image)+": "+str(e));continue
            hashes[digest].append((split,str(image)));approx[phash].append((split,str(image)))
            meta=metadata.get(image)
            if not meta or not meta.get("source_group_id") or meta.get("split")!=split:
                errors.append("Missing/mismatched source group: "+str(image))
            else:
                groups[meta["source_group_id"]].add(split)
                if meta.get("sha256") and meta["sha256"]!=digest:
                    errors.append("Manifest SHA mismatch: "+str(image))
            try:
                rel=image.relative_to(root/"images")
                label=(root/"labels"/rel).with_suffix(".txt")
            except ValueError:
                errors.append("Expected image under dataset images/: "+str(image));continue
            labels=[]
            if label.exists():
                for number,line in enumerate(label.read_text(encoding="utf-8-sig").splitlines(),1):
                    if not line.strip():
                        continue
                    try:
                        items=line.split()
                        if len(items)!=5:
                            raise ValueError("5 fields required")
                        category=int(items[0]);x,y,bw,bh=map(float,items[1:])
                        if category not in names or not all(math.isfinite(v) for v in (x,y,bw,bh)) or not 0<bw<=1 or not 0<bh<=1 or x-bw/2<0 or y-bh/2<0 or x+bw/2>1+1e-6 or y+bh/2>1+1e-6:
                            raise ValueError("Invalid normalized class/box")
                        if bw*w<2 or bh*h<2:
                            warnings.append("Tiny target retained for review: "+str(label)+":"+str(number))
                        counts[names[category]]+=1;labels.append(items)
                    except (ValueError,OverflowError) as e:
                        errors.append(str(label)+":"+str(number)+": "+str(e))
            images.append({"image":str(image),"split":split,"sha256":digest,"boxes":len(labels),
                           "label":str(label),"empty_annotation_requires_manual_review":not bool(labels)})
            split_counts[split]+=1
    for digest,rows in hashes.items():
        if len({split for split,_ in rows})>1:
            errors.append("Exact duplicate image crosses splits: "+digest)
    for group,splits in groups.items():
        if len(splits)>1:
            errors.append("Capture group crosses splits: "+group)
    near=[rows for rows in approx.values() if len({s for s,_ in rows})>1]
    return {"status":"invalid" if errors else "format_checks_passed",
            "errors":errors,"warnings":warnings,"images":len(images),"class_names":names,
            "instances_per_class":dict(counts),"images_per_split":dict(split_counts),
            "exact_duplicate_groups":sum(len(v)>1 for v in hashes.values()),
            "near_duplicate_cross_split_candidates":near,"image_manifest":images,
            "annotation_completeness_verified":False,"manual_annotation_review_required":True,
            "source_group_split_verified":not any("group" in e.lower() for e in errors),
            "accuracy_evaluated":False}
