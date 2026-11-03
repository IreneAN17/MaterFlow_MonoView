"""Real Transformers tensor/API smoke test with random tiny configs, not visual validation.

No pretrained weights or downloads. Production uses from_pretrained exclusively.
"""
import pytest
import numpy as np
from PIL import Image


@pytest.mark.parametrize("case", ["causal", "masked", "decode", "vision"])
def test_efficient_gqa_matches_math_attention(case):
    from types import SimpleNamespace
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA kernel equivalence check")
    from torch.nn.attention import sdpa_kernel, SDPBackend
    from materflow.backends.transformers_backend import efficient_sdpa_attention
    torch.manual_seed(7)
    qlen, klen = (1, 19) if case == "decode" else (19, 19)
    query = torch.randn((1, 4, qlen, 32), device="cuda", dtype=torch.bfloat16)
    key = torch.randn((1, 2, klen, 32), device="cuda", dtype=torch.bfloat16)
    value = torch.randn_like(key)
    mask = torch.ones((qlen, klen), device="cuda", dtype=torch.bool).tril() if case == "masked" else None
    causal = case != "vision"
    actual, _ = efficient_sdpa_attention(SimpleNamespace(is_causal=causal), query, key, value, mask)
    with sdpa_kernel(SDPBackend.MATH):
        expected = torch.nn.functional.scaled_dot_product_attention(
            query.float(), key.float(), value.float(), attn_mask=mask,
            is_causal=bool(qlen > 1 and mask is None and causal), enable_gqa=True)
    torch.testing.assert_close(actual.float(), expected.transpose(1, 2), rtol=.02, atol=.015)


def test_sam2_video_and_dinov2_tensor_contract():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    from transformers import (Sam2Config, Sam2Model, Sam2Processor, Sam2ImageProcessorFast,
        Sam2VideoConfig, Sam2VideoModel, Sam2VideoProcessor, Sam2VideoVideoProcessor,
        Dinov2Config, Dinov2Model, BitImageProcessorFast)
    from materflow.tracking.objects import TrackingModels
    torch.set_num_threads(2)
    vision = {"backbone_config": {"hidden_size": 12, "image_size": [128, 128],
              "embed_dim_per_stage": [12, 24, 48, 96], "num_attention_heads_per_stage": [1, 2, 4, 8],
              "blocks_per_stage": [1, 1, 1, 1], "global_attention_blocks": []},
              "backbone_channel_list": [96, 48, 24, 12],
              "backbone_feature_sizes": [[32, 32], [16, 16], [8, 8]]}
    kwargs = {"vision_config": vision, "prompt_encoder_config": {"image_size": 128},
              "image_size": 128, "memory_attention_num_layers": 1,
              "memory_attention_feed_forward_hidden_size": 256, "memory_attention_rope_feat_sizes": [8, 8]}
    models = TrackingModels.__new__(TrackingModels)
    models.torch, models.device, models.dtype = torch, "cpu", torch.float32
    models.image_model = Sam2Model(Sam2Config(**kwargs)).eval()
    image_processor = Sam2ImageProcessorFast(size={"height": 128, "width": 128}, mask_size={"height": 32, "width": 32})
    models.image_processor = Sam2Processor(image_processor)
    models.video_model = Sam2VideoModel(Sam2VideoConfig(**kwargs)).eval()
    models.video_processor = Sam2VideoProcessor(image_processor,
        Sam2VideoVideoProcessor(size={"height": 128, "width": 128}))
    models.dino_processor = BitImageProcessorFast(size={"shortest_edge": 32}, crop_size={"height": 32, "width": 32})
    models.dino_model = Dinov2Model(Dinov2Config(hidden_size=32, num_hidden_layers=1, num_attention_heads=4,
                                              image_size=32, patch_size=8)).eval()
    image = Image.fromarray(np.zeros((48, 64, 3), dtype=np.uint8))
    mask, vector = models.segment_and_embed(image, {"box_xyxy": [.1, .1, .7, .8]})
    assert mask.shape == (48, 64) and mask.dtype == bool
    assert len(vector) == 32 and np.isfinite(vector).all()
    boxes = models.propagate([image, image.copy()], 0, mask)
    assert set(boxes) == {0, 1}


def test_json_byte_fallback_unicode_never_allows_premature_eos():
    import json
    import torch
    from materflow.backends.transformers_backend import json_tokenizer_data,json_prefix_function
    from materflow.perception.relations import scene_response_schema
    class ByteTokenizerFixture:
        all_special_ids=[256]
        eos_token_id=256
        def __len__(self):return 257
        def encode(self,text,**kwargs):return list(text.encode("utf8"))
        def decode(self,ids,**kwargs):return bytes(i for i in ids if i<256).decode("utf8",errors="replace")
    tokenizer=ByteTokenizerFixture();data=json_tokenizer_data(tokenizer)
    assert not {9,10,13}&{token for token,_,_ in data.regular_tokens}
    text="蓝色丁腈手套 Ω μ −7.297 g 🧪 𠀀\n第二行\t45%"
    value={"objects":[{"local_id":"obj_1","object_class":None,"description":text,"region_hint_xyxy":None}],
           "relations":[],"texts":[],"flows":[],"states":[]}
    raw=json.dumps(value,ensure_ascii=False,separators=(",",":"))
    allowed=json_prefix_function(tokenizer,data,scene_response_schema())
    ids=tokenizer.encode("Decoder fixture:")
    for token in tokenizer.encode(raw):
        options=allowed(0,torch.tensor(ids,dtype=torch.long))
        assert tokenizer.eos_token_id not in options
        assert token in options
        ids.append(token)
    assert tokenizer.eos_token_id in allowed(0,torch.tensor(ids,dtype=torch.long))
    assert json.loads(raw)==value
