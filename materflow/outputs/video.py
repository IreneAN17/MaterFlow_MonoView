"""Render model evidence over the original video. Human reference files are never read."""
import argparse
from bisect import bisect_right
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import av
from PIL import ImageDraw, ImageFont
from ..schema import FrameRecord, read_json, read_jsonl, write_json, file_hash


def _font(size):
    for path in (Path('C:/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/arial.ttf')):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _color(track):
    value = sum(ord(c) for c in track)
    return (70 + value*37 % 170, 70 + value*71 % 170, 70 + value*19 % 170)


def render_annotated_videos(result, frames, context):
    config, output = context['config'], context['config']['output_dir']
    observations = result['observations']
    by_id = {o['observation_id']: o for o in observations}
    by_frame = defaultdict(list)
    evidence_index = context.get('evidence_index', {})
    for obs in observations:
        for ref in obs['evidence_refs']:
            evidence = evidence_index.get(ref, {})
            if evidence.get('frame_id') is not None:
                key = (obs['source_id'], evidence['frame_id'])
                if obs not in by_frame[key]:
                    by_frame[key].append(obs)
    event_frames = defaultdict(list)
    for event in result['events']:
        for ref in event['evidence_refs']:
            for visual in by_id[ref]['evidence_refs']:
                ev = evidence_index.get(visual, {})
                if ev.get('frame_id') is not None:
                    key = (event['source_id'], ev['frame_id'])
                    if event not in event_frames[key]:
                        event_frames[key].append(event)
    products = []
    for sid, source_path in context['source_paths'].items():
        if source_path.is_dir():
            continue
        frame_graphs = read_json(output / 'frame_graphs/v1' / (sid + '.json'))
        video_graph = read_json(output / 'video_graphs/v1' / (sid + '.json'))
        detection_to_track = video_graph['detection_to_track']
        identities = context.get('identities', {})
        sampled = sorted((f for f in frames if f.source_id == sid), key=lambda f: f.source_frame_index)
        source_indices = [f.source_frame_index for f in sampled]
        graphs = {g['frame']['frame_id']: g for g in frame_graphs}
        path = output / (sid + '_annotated.mp4')
        font, small = _font(24), _font(20)
        count = 0
        with av.open(str(source_path)) as incoming, av.open(str(path), 'w') as outgoing:
            video_in = incoming.streams.video[0]
            rate = video_in.average_rate or Fraction(30, 1)
            stream = outgoing.add_stream('libx264', rate=rate)
            stream.width, stream.height = video_in.width, video_in.height
            stream.pix_fmt = 'yuv420p'
            stream.options = {'crf': '21', 'preset': 'fast'}
            stream.codec_context.thread_count = 4
            origin = video_in.start_time if video_in.start_time is not None else 0
            audio_out = {s.index: outgoing.add_stream_from_template(s) for s in incoming.streams.audio}
            for packet in incoming.demux():
                if packet.stream.type == 'audio':
                    if packet.dts is not None:
                        packet.stream = audio_out[packet.stream.index]
                        outgoing.mux(packet)
                    continue
                if packet.stream.index != video_in.index:
                    continue
                for decoded in packet.decode():
                    time_s = float((decoded.pts-origin)*decoded.time_base) if decoded.pts is not None else None
                    image = decoded.to_image().convert('RGB')
                    draw = ImageDraw.Draw(image)
                    i = bisect_right(source_indices, count) - 1
                    selected = sampled[i] if i >= 0 else None
                    lag = time_s-selected.source_time_s if selected and time_s is not None and selected.source_time_s is not None else None
                    valid = selected is not None and lag is not None and 0 <= lag <= 1/config['sample_fps'] + .04
                    header = f'AUTO | {sid} | source {time_s:.3f}s | {config["sample_fps"]} fps detection/tracking | {config.get("caption_fps") or config["sample_fps"]} fps Qwen' if time_s is not None else f'AUTO | {sid}'
                    lines = [header]
                    if valid:
                        lines.append(f'Boxes are from sampled evidence @{selected.source_time_s:.3f}s; display age {lag:.3f}s')
                        graph = graphs[selected.frame_id]
                        for det in graph['detections']:
                            tid = detection_to_track.get(det['detection_id'], det['detection_id'])
                            oid = identities.get(tid, {}).get('object_id')
                            x1,y1,x2,y2 = [int(v*(image.width if n%2 == 0 else image.height)) for n,v in enumerate(det['box_xyxy'])]
                            color = _color(tid)
                            draw.rectangle((x1,y1,x2,y2), outline=color, width=3)
                            label = f'{det["object_class"]} | {oid or tid[:9]} | det {det["score"]:.2f}'
                            draw.text((x1,max(0,y1-24)), label, font=small, fill=color, stroke_width=1, stroke_fill=(0,0,0))
                        raw_texts = [o['payload'].get('raw_text') for o in by_frame[(sid, selected.frame_id)] if o['signal_type'] == 'ocr_text']
                        if raw_texts:
                            lines.append('OCR: ' + ' | '.join(dict.fromkeys(t for t in raw_texts if t))[:160])
                        events = event_frames[(sid, selected.frame_id)]
                        if events:
                            lines.append('Event evidence: ' + ' | '.join(dict.fromkeys(e['action']+' '+e['event_id'][:9] for e in events))[:160])
                    else:
                        lines.append('No model evidence attached to this source frame.')
                    lines.append('Automatic draft. Unknown identities/boundaries remain unresolved. No human annotations overlaid.')
                    draw.rectangle((0,0,image.width,10+31*len(lines)), fill=(12,22,31))
                    for n,line in enumerate(lines):
                        draw.text((12,5+31*n),line,font=font,fill=(240,246,250))
                    encoded = av.VideoFrame.from_image(image)
                    encoded.pts, encoded.time_base = decoded.pts, decoded.time_base
                    for out_packet in stream.encode(encoded):
                        outgoing.mux(out_packet)
                    count += 1
                    if count % 300 == 0:
                        print(f'annotated video {sid}: {count}/{video_in.frames or "?"} source frames',flush=True)
            for out_packet in stream.encode():
                outgoing.mux(out_packet)
        write_json(path.with_suffix('.json'), {'source_id':sid,'input_path':str(source_path),'input_sha256':file_hash(source_path),
                   'output_video':str(path),'source_frames_encoded':count,'analysis_sample_fps':config['sample_fps'],
                   'caption_sample_fps':config.get('caption_fps') or config['sample_fps'],
                   'overlay_policy':'Original timeline. Boxes held from last sampled frame for <= one sample period, with displayed age. Event labels mark evidence, not complete intervals.',
                   'human_reference_used':False})
        products.extend([path,path.with_suffix('.json')])
    return products


def main():
    from ..config import load_config
    from ..materials.context import load_run_context
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    config=load_config(args.config)
    context=load_run_context(config)
    output=config['output_dir']
    result={'observations':read_jsonl(output/'observations.jsonl'),**read_json(output/'derived.json')}
    context['evidence_index']=read_json(output/'evidence_index.json')
    context['identities']=read_json(output/'identities.json')
    frames=[FrameRecord(**f) for f in read_jsonl(output/'frame_index.jsonl')]
    render_annotated_videos(result,frames,context)


if __name__ == '__main__':
    main()
