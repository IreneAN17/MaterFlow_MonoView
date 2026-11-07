"""Bounded KLT tracking: real optical updates are distinct from predictions."""
from collections import Counter
from copy import deepcopy
import cv2
import numpy as np
from ..schema import stable_id
from ..perception.grounding import box_iou

def gray_image(image, width):
    w, h = image.size
    return cv2.cvtColor(np.asarray(image.resize((width, round(h*width/w)))), cv2.COLOR_RGB2GRAY)

def histogram(image, box):
    w,h = image.size
    crop = np.asarray(image.crop([int(v*(w if i%2==0 else h)) for i,v in enumerate(box)]).resize((24,24)))
    hist = np.concatenate([np.histogram(crop[:,:,i], bins=8, range=(0,256))[0] for i in range(3)]).astype(float)
    return (hist / max(np.linalg.norm(hist), 1e-8)).tolist()

def compatible(a,b):
    return a == b or ("container" in (a,b) and {a,b} <= {"container","beaker","bottle","weighing cup"})

def local_motion(previous, current):
    if previous is None or previous.shape != current.shape:
        return 1., None, 0.
    pts = cv2.goodFeaturesToTrack(previous, 250, .02, 8)
    matrix = None
    if pts is not None and len(pts) >= 12:
        nxt, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, pts, None)
        if nxt is not None:
            good = status[:,0].astype(bool)
            if good.sum() >= 12:
                matrix, _ = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC,
                                                       ransacReprojThreshold=3.)
    h,w = current.shape
    aligned = cv2.warpAffine(previous, matrix, (w,h)) if matrix is not None else previous
    diff = np.abs(current.astype(float)-aligned.astype(float))/255.
    # Regional maximum retains small object/display changes diluted by global means.
    scores = [float(np.mean(block)) for block in
              (diff[y:y+h//6,x:x+w//8] for y in range(h//12, h-h//6, h//6)
               for x in range(w//16, w-w//8, w//8))]
    camera = float(np.linalg.norm(matrix[:,2])/max(w,h)) if matrix is not None else 0.
    return max(scores, default=float(diff.mean())), matrix, camera

class KLTTracker:
    def __init__(self,c):
        self.c = c
        cv2.setNumThreads(c.get("torch_num_threads",4))
        self.active = {}
        self.previous = None
        self.shot = None
        self.metrics = Counter()
        self.peak_active = 0

    def restore(self, state, previous):
        self.active = deepcopy(state.get("active", {}))
        self.previous = previous
        self.shot = state.get("shot")
        self.metrics = Counter(state.get("metrics", {}))
        self.peak_active = state.get("peak_active", 0)

    def state(self):
        return {"active": deepcopy(self.active), "shot": self.shot, "metrics": dict(self.metrics),
                "peak_active": self.peak_active}

    def update(self, frame, image, current, detections, refreshed, shot):
        now = frame["source_time_s"]
        if self.shot != shot:
            self.active = {}
            self.previous = None
        self.shot = shot
        h,w = current.shape
        propagated = {}
        if self.previous is not None:
            point_sets=[]
            for tid,t in list(self.active.items()):
                if now-t["last_visual_s"] > self.c["track_gap_s"]:
                    continue
                mask = np.zeros_like(current)
                x1,y1,x2,y2 = [int(v*(w if i%2==0 else h)) for i,v in enumerate(t["box"])]
                mask[max(0,y1):min(h,y2),max(0,x1):min(w,x2)] = 255
                pts = cv2.goodFeaturesToTrack(self.previous, 40, .015, 4, mask=mask)
                if pts is None or len(pts) < self.c["flow_min_points"]:
                    self.metrics["flow_insufficient_features"] += 1
                    continue
                point_sets.append((tid,t,pts))
            if point_sets:
                combined=np.concatenate([p for _,_,p in point_sets])
                next_all,status_all,error_all=cv2.calcOpticalFlowPyrLK(self.previous,current,combined,None)
                self.metrics["optical_batches"]+=1
            else:
                next_all=None
            offset=0
            for tid,t,pts in point_sets:
                end=offset+len(pts)
                if next_all is None:
                    continue
                nxt=next_all[offset:end];status=status_all[offset:end]
                error=error_all[offset:end] if error_all is not None else None
                offset=end
                good = status[:,0].astype(bool) & np.isfinite(nxt[:,0]).all(axis=1)
                if error is not None:
                    good &= error[:,0] < 25
                if good.sum() < self.c["flow_min_points"]:
                    continue
                delta = nxt[good,0]-pts[good,0]
                median = np.median(delta,axis=0)
                residual = np.linalg.norm(delta-median,axis=1)
                keep = residual < max(2., float(np.median(residual))*2.5)
                if keep.mean() < self.c["flow_inlier_fraction"] or keep.sum() < self.c["flow_min_points"]:
                    continue
                dx,dy = np.median(delta[keep],axis=0)/[w,h]
                b = t["box"]
                box = [max(0.,min(1.,v+(dx if i%2==0 else dy))) for i,v in enumerate(b)]
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                propagated[tid] = box
                t["box"],t["last_visual_s"] = box, now
                self.metrics["optical_updates"] += 1
        active = {tid:t for tid,t in self.active.items() if now-t["last_visual_s"] <= self.c["track_gap_s"]}
        pairs = {}
        features = [histogram(image,d["box_xyxy"]) for d in detections]
        for di,d in enumerate(detections):
            for tid,t in active.items():
                if not compatible(t["object_class"], d["object_class"]):
                    continue
                overlap = box_iou(t["box"], d["box_xyxy"])
                similarity = float(np.dot(features[di], t["feature"]))
                if overlap >= .25 and similarity >= .65:
                    pairs[di,tid] = overlap*.8 + similarity*.2
        matched,used = {},set()
        for (di,tid),score in sorted(pairs.items(),key=lambda x:-x[1]):
            if di in matched or tid in used:
                continue
            rivals=[v for (d,t),v in pairs.items() if (d==di and t!=tid) or (t==tid and d!=di)]
            if rivals and score-max(rivals) < .06:
                self.metrics["ambiguous_associations"] += 1
                continue
            matched[di], _ = tid, used.add(tid)
        actual = {}
        for di,d in enumerate(detections):
            tid = matched.get(di) or stable_id("T", frame["source_id"],shot,d["detection_id"])
            t = active.setdefault(tid, {"track_id":tid,"object_class":d["object_class"],
                    "first_frame":frame,"first_s":now,"shot_id":shot,"count":0,"feature":features[di]})
            if t["object_class"] == "container" and d["object_class"] != "container":
                t["object_class"] = d["object_class"]
            t.update(box=d["box_xyxy"],last_visual_s=now,last_detection_s=now,last_frame=frame,
                     feature=features[di],count=t["count"]+1)
            d["track_id"] = tid
            actual[tid] = d
        shown = []
        for tid,t in active.items():
            if tid in actual:
                kind = "detector"
            elif tid in propagated:
                kind = "optical_flow"
            else:
                continue  # An unverified prediction never becomes a visibility observation.
            shown.append({"track_id":tid,"object_class":t["object_class"],"box_xyxy":t["box"],
                          "evidence_kind":kind,"source_time_s":now,
                          "last_detection_s":t["last_detection_s"],
                          "support_points":t["count"],"shot_id":shot})
        self.active, self.previous = active, current
        self.peak_active = max(self.peak_active,len(active))
        return shown

class OfficialTrackerAdapter:
    """Optional, actual Ultralytics tracker over detector outputs; never silently substitutes KLT."""
    def __init__(self,c):
        try:
            from ultralytics.trackers.byte_tracker import BYTETracker
            from ultralytics.trackers.bot_sort import BOTSORT
        except ImportError as e:
            raise RuntimeError("Requested optional ByteTrack/BoT-SORT is not installed; use documented detector environment or explicit klt") from e
        from types import SimpleNamespace
        args = SimpleNamespace(track_high_thresh=.4, track_low_thresh=.1, new_track_thresh=.4,
               track_buffer=max(1,round(c["track_gap_s"]*c["sample_fps"])), match_thresh=.8,
               fuse_score=True, gmc_method="sparseOptFlow", with_reid=False,
               proximity_thresh=.5, appearance_thresh=.8, model="auto")
        cls = BYTETracker if c["tracker_backend"]=="bytetrack" else BOTSORT
        self.model = cls(args, frame_rate=c["sample_fps"])
        self.c = c
        self.names = {}
        self.metrics = Counter()
        self.peak_active = 0
        self.shot = None

    def update(self, frame,image,current,detections,refreshed,shot):
        from ultralytics.engine.results import Boxes
        if self.shot != shot:
            self.model.reset()
        self.shot=shot
        if not refreshed:
            raise ValueError("Official frame-based tracker requires actual detections each sampled frame; set both detection rates to sample_fps")
        names = sorted({d["object_class"] for d in detections}|set(self.names))
        for n in names:
            self.names.setdefault(n,len(self.names))
        inverse = {v:k for k,v in self.names.items()}
        w,h=image.size
        a=np.asarray([[*[v*(w if i%2==0 else h) for i,v in enumerate(d["box_xyxy"])],
                       d["score"],self.names[d["object_class"]]] for d in detections],dtype=np.float32).reshape(-1,6)
        outputs=self.model.update(Boxes(a,(h,w)),np.asarray(image)[:,:,::-1])
        shown=[]
        for row in outputs:
            x1,y1,x2,y2,track,score,category,index=map(float,row[:8])
            index=int(index)
            if not 0<=index<len(detections):
                continue
            tid=stable_id("T",frame["source_id"],shot,int(track))
            detections[index]["track_id"]=tid
            shown.append({"track_id":tid,"object_class":inverse[int(category)],
                          "box_xyxy":[x1/w,y1/h,x2/w,y2/h],"evidence_kind":"detector",
                          "source_time_s":frame["source_time_s"],"last_detection_s":frame["source_time_s"],
                          "support_points":1,"shot_id":shot})
        self.metrics["official_updates"]+=1
        self.peak_active=max(self.peak_active,len(shown))
        return shown

def create_tracker(c):
    if c["tracker_backend"]=="klt":
        return KLTTracker(c)
    if c["detection_idle_fps"] != c["sample_fps"] or c["detection_active_fps"] != c["sample_fps"]:
        raise ValueError("Official trackers require real per-sample detections in this adapter")
    return OfficialTrackerAdapter(c)
