from collections import Counter,defaultdict
from dataclasses import asdict
from pathlib import Path
import re,time,importlib.metadata,inspect
import numpy as np
from PIL import Image
from ..schema import stable_id,write_json,read_json,file_hash,normalize
from ..perception.grounding import GroundingDINO,box_iou,merge_frame_detections
from ..tracking.objects import TrackingModels,mask_box,cosine

VESSELS={"container","beaker","weighing cup","bottle"}

def filter_conflicts(detections,config):
    ordered=merge_frame_detections(detections);removed=set();reasons=[]
    for i,a in enumerate(ordered):
        for j,b in enumerate(ordered[i+1:],i+1):
            if a["object_class"]==b["object_class"] or not {a["object_class"],b["object_class"]}<=VESSELS:continue
            if box_iou(a["box_xyxy"],b["box_xyxy"])<config.get("conflict_iou",.9):continue
            if "container" in (a["object_class"],b["object_class"]):
                generic=i if a["object_class"]=="container" else j
                specific=j if generic==i else i
                if ordered[specific]["score"]>=ordered[generic]["score"]-.04:removed.add(generic)
                else:removed.update((i,j))
            elif abs(a["score"]-b["score"])<config.get("conflict_margin",.04):removed.update((i,j))
            else:removed.add(j if a["score"]>=b["score"] else i)
    for i in removed:reasons.append({**ordered[i],"reason":"mutually_exclusive_category_conflict"})
    return [d for i,d in enumerate(ordered) if i not in removed],reasons

def canonical_label(raw,classes):
    words=set(re.findall(r"[a-z]+",normalize(str(raw))))-{"a","an","the"}
    exact=[c for c in classes if normalize(str(raw))==c]
    if len(exact)==1:return exact[0]
    matches=[c for c in classes if words and words<=set(c.split())]
    return matches[0] if len(matches)==1 else None

class MultiDetector(GroundingDINO):
    def __init__(self,config):
        super().__init__(config);self.calls=0;self.ambiguous=[]
        self.version.update(transformers=importlib.metadata.version("transformers"),preprocessing=self.processor.image_processor.to_dict(),dtype=str(self.model.dtype),implementation_sha256=file_hash(inspect.getfile(GroundingDINO)))
        self.torch.set_num_threads(config.get("torch_num_threads",4))
    def detect_classes(self,image,classes,all_classes=None):
        if not classes:return []
        text=". ".join(classes)+"."
        inputs=self.processor(images=image,text=text,return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():out=self.model(**inputs)
        self.calls+=1
        results=self.processor.post_process_grounded_object_detection(out,inputs.input_ids,threshold=self.config.get("box_threshold",.4),text_threshold=self.config.get("text_threshold",.25),target_sizes=[image.size[::-1]])[0]
        labels=results.get("text_labels",results.get("labels",[])); records=[];w,h=image.size
        for pixels,score,label in zip(results["boxes"].tolist(),results["scores"].tolist(),labels):
            category=canonical_label(label,all_classes or classes)
            if category is None or category not in classes:
                self.ambiguous.append({"raw_label":str(label),"score":score,"box_px":pixels});continue
            if score<self.config.get("class_thresholds",{}).get(category,self.config.get("box_threshold",.4)):continue
            box=[max(0,min(1,v/(w if k%2==0 else h))) for k,v in enumerate(pixels)]
            if box[2]<=box[0] or box[3]<=box[1]:continue
            roi=self.config.get("work_roi",[0,0,1,1]);cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
            if not roi[0]<=cx<=roi[2] or not roi[1]<=cy<=roi[3]:continue
            records.append({"object_class":category,"box_xyxy":box,"score":float(score),"evidence_kind":"detector"})
        return records

def assignments(graph):
    result={}
    for entry in graph["captions"]:
        used=set()
        for obj in entry["response"]["objects"]:
            options=[d for d in graph["detections"] if d["object_class"]==obj["object_class"] and d["detection_id"] not in used]
            hint=obj.get("region_hint_xyxy");chosen=None
            if hint and options:
                ranked=sorted(((box_iou(hint,d["box_xyxy"]),d) for d in options),key=lambda x:-x[0])
                if ranked[0][0]>.2 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>.1):chosen=ranked[0][1]
            elif len(options)==1 and sum(o["object_class"]==obj["object_class"] for o in entry["response"]["objects"])==1:chosen=options[0]
            key=entry["request_id"]+":"+obj["local_id"];result[key]=chosen["detection_id"] if chosen else None
            if chosen:used.add(chosen["detection_id"])
    graph["assignments"]=result

def ground(frames,captions,context,progress):
    c=context["config"];output=c["output_dir"];detector=MultiDetector(c);by_frame=defaultdict(list)
    for e in captions:by_frame[(e["frame"]["source_id"],e["frame"]["frame_id"])].append(e)
    classes=sorted(context["classes"]);shots=read_json(output/"shots.json");motion=read_json(output/"motion_scores.json")
    graphs=[];rejected=[];last={};oldshot={};query_total=0;cache=output/"cache/ground";cache.mkdir(parents=True,exist_ok=True);hits=0;start=time.perf_counter()
    for frame in frames:
        key=(frame.source_id,frame.frame_id);shot=shots[f"{frame.source_id}:{frame.frame_id}"]
        refresh=shot!=oldshot.get(frame.source_id) or frame.source_time_s-last.get(frame.source_id,-1e9)>=1/c.get("detection_fps",c["sample_fps"])-1e-6 or (motion[f"{frame.source_id}:{frame.frame_id}"]>.16 and frame.source_time_s-last.get(frame.source_id,-1e9)>=.4)
        detections=[]
        neighbors=sorted((e for e in captions if e["frame"]["source_id"]==frame.source_id and shots.get(f"{frame.source_id}:{e['frame']['frame_id']}")==shot),key=lambda e:abs(e["frame"]["source_time_s"]-frame.source_time_s))[:2]
        # Category priors only: no text, states, flows or boxes propagated between frames.
        queries=sorted({o["object_class"] for e in neighbors for o in e["response"]["objects"] if o["object_class"] in context["classes"]}) or classes
        if refresh:
            query_total+=len(queries)
            signature=stable_id("GD",file_hash(frame.image_path),detector.version,queries,{k:c.get(k) for k in ("box_threshold","text_threshold","class_thresholds","work_roi","conflict_iou","conflict_margin")},file_hash(Path(__file__)))
            path=cache/(signature+".json")
            cached=read_json(path) if path.is_file() else None
            if isinstance(cached,dict) and cached.get("signature")==signature and cached.get("content_hash")==stable_id("",cached.get("detections")):
                data=cached["detections"];hits+=1
            else:
                ambiguity_start=len(detector.ambiguous)
                with Image.open(frame.image_path) as image:data=detector.detect_classes(image.convert("RGB"),queries,classes)
                for item in detector.ambiguous[ambiguity_start:]:item.update(source_id=frame.source_id,frame_id=frame.frame_id,image_path=frame.image_path)
                write_json(path,{"signature":signature,"detections":data,"content_hash":stable_id("",data),"model":detector.version})
            detections,removed=filter_conflicts(data,c);rejected.extend({**r,"source_id":frame.source_id,"frame_id":frame.frame_id} for r in removed)
            last[frame.source_id]=frame.source_time_s
        oldshot[frame.source_id]=shot
        for i,d in enumerate(detections):d["detection_id"]=stable_id("D",frame.source_id,frame.frame_id,d["object_class"],d["box_xyxy"])
        graph={"frame":asdict(frame),"detections":detections,"captions":by_frame[key],"shot_id":shot,"detector_refreshed":refresh,"detector_query_classes":queries if refresh else []}
        assignments(graph);graphs.append(graph)
        if len(graphs)%10==0:progress.update(f"ground: {len(graphs)}/{len(frames)} frames; {detector.calls} forwards; {hits} cache hits")
    write_json(output/"ground_filter.json",{"rejected":rejected,"ambiguous_text_labels":detector.ambiguous})
    write_json(output/"ground_metrics.json",{"forward_calls":detector.calls,"queried_phrases":query_total,"cache_hits":hits,"frames":len(frames),"detections":sum(len(g["detections"]) for g in graphs),"elapsed_s":time.perf_counter()-start,"model":detector.version})
    return graphs

class BatchTrackingModels(TrackingModels):
    def __init__(self,c):
        super().__init__(c)
        self.version.update(transformers=importlib.metadata.version("transformers"),preprocessing={"sam2":self.image_processor.image_processor.to_dict(),"dinov2":self.dino_processor.to_dict()},device=self.device,dtype=str(self.dtype),implementation_sha256=file_hash(inspect.getfile(TrackingModels)))
        self.metrics=Counter();self.torch.set_num_threads(c.get("torch_num_threads",4));self.video_model.vision_encoder.register_forward_hook(lambda *args:self.metrics.update(["sam_video_encoder_forwards"]));self.cache=c["output_dir"]/"cache/features";self.cache.mkdir(parents=True,exist_ok=True)
    def batch_features(self,image,detections,image_path):
        if not detections:return []
        key=stable_id("features",file_hash(image_path),[(d["detection_id"],d["box_xyxy"]) for d in detections],self.version,file_hash(Path(__file__)))
        path=self.cache/(key+".npz")
        meta=path.with_suffix(".json")
        if path.is_file() and meta.is_file():
            valid=False
            try:
                manifest=read_json(meta)
                if manifest.get("sha256")==file_hash(path) and manifest.get("signature")==key:
                    with np.load(path,allow_pickle=False) as data:
                        valid=(str(data["signature"])==key and data["masks"].shape==(len(detections),image.height,image.width) and len(data["features"])==len(detections) and np.isfinite(data["features"]).all())
                        if valid:result=[(m,v.tolist()) for m,v in zip(data["masks"],data["features"])]
            except (OSError,ValueError,KeyError):valid=False
            if valid:self.metrics["feature_cache_hits"]+=1;return result
            self.metrics["feature_cache_rejected"]+=1
        w,h=image.size;boxes=[[v*(w if k%2==0 else h) for k,v in enumerate(d["box_xyxy"])] for d in detections]
        inputs=self.image_processor(images=image,input_boxes=[boxes],return_tensors="pt").to(self.device);inputs["pixel_values"]=inputs["pixel_values"].to(self.dtype)
        with self.torch.inference_mode():out=self.image_model(**inputs,multimask_output=False)
        self.metrics["sam_image_encoder_forwards"]+=1
        masks=self.image_processor.post_process_masks(out.pred_masks.cpu(),inputs["original_sizes"].cpu())[0][:,0].numpy().astype(bool)
        crops=[]
        for box,mask in zip(boxes,masks):
            crop=image.crop(box).convert("RGB");isolated=Image.new("RGB",crop.size,(127,127,127));isolated.paste(crop,mask=Image.fromarray(mask).crop(box).resize(crop.size));crops.append(isolated)
        features=[]
        for start in range(0,len(crops),8):
            batch=self.dino_processor(images=crops[start:start+8],return_tensors="pt").to(self.device)
            with self.torch.inference_mode():vectors=self.dino_model(**batch).last_hidden_state[:,0].float().cpu().numpy()
            vectors/=np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12);features.extend(vectors);self.metrics["dino_batch_forwards"]+=1
        temporary=path.with_name(path.stem+".pending.npz")
        np.savez_compressed(temporary,masks=masks,features=np.asarray(features),signature=np.asarray(key));temporary.replace(path)
        write_json(meta,{"signature":key,"sha256":file_hash(path)})
        self.metrics["objects_segmented"]+=len(detections)
        return [(m,v.tolist()) for m,v in zip(masks,features)]

    def video_boxes(self,session,index):
        with self.torch.inference_mode():out=self.video_model(inference_session=session,frame_idx=index)
        self.metrics["sam_video_decodes"]+=1
        masks=self.video_processor.post_process_masks([out.pred_masks],original_sizes=[[session.video_height,session.video_width]],binarize=True)[0]
        return {oid:mask_box(masks[i,0].cpu().numpy().astype(bool)) for i,oid in enumerate(out.object_ids)}

def compatible(a,b):return a==b or ("container" in (a,b) and a in VESSELS and b in VESSELS)

def track(graphs,context,progress):
    c=context["config"];models=BatchTrackingModels(c);output=c["output_dir"];tracks=[];mapping={};local={};display={};start=time.perf_counter();chunks=[]
    # Bounded non-overlapping chunks; no frame is segmented twice at a window overlap.
    for graph in graphs:
        if not chunks or chunks[-1][0]["shot_id"]!=graph["shot_id"] or len(chunks[-1])>=c.get("track_chunk_frames",40):chunks.append([])
        chunks[-1].append(graph)
    previous=[];lastshot=None
    for ci,chunk in enumerate(chunks):
        active=previous if chunk[0]["shot_id"]==lastshot else [];images=[Image.open(g["frame"]["image_path"]).convert("RGB") for g in chunk]
        session=models.video_processor.init_video_session(video=images,inference_device=models.device,video_storage_device="cpu",inference_state_device=models.device,dtype=models.dtype)
        models.metrics["multiobject_sessions"]+=1;video_ids={};next_id=1
        for index,(graph,image) in enumerate(zip(chunk,images)):
            frame=graph["frame"];now=frame["source_time_s"];dets=graph["detections"];predicted={}
            if index and session.obj_ids:
                predicted={video_ids[i]["track_id"]:b for i,b in models.video_boxes(session,index).items() if i in video_ids and b}
            prepared=models.batch_features(image,dets,frame["image_path"]);scores={}
            for di,(det,(mask,feature)) in enumerate(zip(dets,prepared)):
                for ti,t in enumerate(active):
                    if now-t["last_time_s"]>c.get("max_track_gap_s",1.) or not compatible(t["object_class"],det["object_class"]):continue
                    similarity=cosine(feature,t["feature"]);overlap=box_iou(predicted.get(t["track_id"],t["last_box"]),det["box_xyxy"])
                    if similarity>=c.get("appearance_threshold",.72) and overlap>=.2:scores[di,ti]=similarity+.3*overlap
            matched={};used=set()
            for (di,ti),score in sorted(scores.items(),key=lambda pair:-pair[1]):
                if di in matched or ti in used:continue
                rivals=[v for (d,t),v in scores.items() if (d==di and t!=ti) or (t==ti and d!=di)]
                if rivals and score-max(rivals)<.035:continue
                matched[di]=ti;used.add(ti)
            ids=[];seed_boxes=[]
            for di,(det,(mask,feature)) in enumerate(zip(dets,prepared)):
                if di in matched:
                    t=active[matched[di]];t["feature"]=(.8*np.asarray(t["feature"])+.2*np.asarray(feature)).tolist()
                else:
                    t={"track_id":stable_id("T",c["run_id"],graph["shot_id"],det["detection_id"]),"object_class":det["object_class"],"feature":feature,"history":[],"shot_id":graph["shot_id"],"predicted_history":[]}
                    tracks.append(t);active.append(t)
                t.update(last_time_s=now,last_box=det["box_xyxy"])
                t["history"].append({**frame,**det,"association":"appearance_and_spatial_match" if di in matched else "new_track"})
                mapping[det["detection_id"]]=t["track_id"]
                vid=next((i for i,tr in video_ids.items() if tr is t),None)
                if vid is None:vid=next_id;next_id+=1;video_ids[vid]=t
                ids.append(vid);w,h=image.size;seed_boxes.append([v*(w if k%2==0 else h) for k,v in enumerate(det["box_xyxy"])])
            if ids:
                models.video_processor.add_inputs_to_inference_session(session,frame_idx=index,obj_ids=ids,input_boxes=[seed_boxes],original_size=(image.height,image.width))
                predicted.update({video_ids[i]["track_id"]:b for i,b in models.video_boxes(session,index).items() if i in video_ids and b})
            shown=[]
            for t in active:
                if now-t["last_time_s"]>c.get("max_track_gap_s",1.):continue
                actual=next((d for d in dets if mapping.get(d["detection_id"])==t["track_id"]),None)
                box=actual["box_xyxy"] if actual else predicted.get(t["track_id"])
                if box:
                    row={"track_id":t["track_id"],"object_class":t["object_class"],"box_xyxy":box,"evidence_kind":"detector" if actual else "SAM2_propagated","source_time_s":now}
                    shown.append(row)
                    if not actual:t["predicted_history"].append({**frame,**row})
            display[f"{frame['source_id']}:{frame['frame_id']}"]=shown
            for key,did in graph["assignments"].items():local[key]=mapping.get(did)
        previous=[t for t in active if chunk[-1]["frame"]["source_time_s"]-t["last_time_s"]<=c.get("max_track_gap_s",1.)];lastshot=chunk[0]["shot_id"]
        for image in images:image.close()
        del session
        progress.update(f"consolidate: chunk {ci+1}/{len(chunks)}, {len(tracks)} tracks")
    for t in tracks:
        votes=Counter(h["object_class"] for h in t["history"] if h["object_class"]!="container")
        if votes:t["object_class"]=votes.most_common(1)[0][0]
    metrics={**models.metrics,"global_tracks":len(tracks),"single_frame_tracks":sum(len(t["history"])==1 for t in tracks),"median_history":float(np.median([len(t["history"]) for t in tracks])) if tracks else 0,"elapsed_s":time.perf_counter()-start,"models":models.version}
    write_json(output/"tracking_metrics.json",metrics)
    return {"tracks":tracks,"frame_graphs":graphs,"detection_to_track":mapping,"local_to_track":local,"display":display}
