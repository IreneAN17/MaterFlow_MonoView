"""Optional cache setup; downloads the exact pinned checkpoints, no inference."""
from pathlib import Path
from huggingface_hub import snapshot_download
ROOT=Path(__file__).resolve().parents[2]
MODELS={"Qwen/Qwen3.5-4B":"851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a","IDEA-Research/grounding-dino-tiny":"a2bb814dd30d776dcf7e30523b00659f4f141c71","facebook/sam2.1-hiera-tiny":"de431c4043854a71d8101e17995dfe596bf101a5","facebook/dinov2-small":"ed25f3a31f01632728cabb09d1542f84ab7b0056"}
if __name__=='__main__':
    for name,revision in MODELS.items():
        path=snapshot_download(name,revision=revision,cache_dir=str(ROOT/'models/hub'))
        print(name,revision,path,flush=True)
