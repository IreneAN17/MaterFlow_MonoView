"""Transformers 5.3.0 multimodal chat API; no model-internal rope modifications."""
from pathlib import Path
import gc
import os
import time
from .base import VisionBackend


def json_tokenizer_data(tokenizer):
    """LMFE public core API adapter for Transformers 5 tokenizer import paths.

    Token decoding follows LM Format Enforcer's MIT-licensed Transformers adapter;
    see docs/third_party_notices.md. No upstream modules are monkey-patched.
    """
    from lmformatenforcer import TokenEnforcerTokenizerData
    zero = tokenizer.encode("0")[-1]
    special = set(tokenizer.all_special_ids)
    regular = []
    for token_id in range(len(tokenizer)):
        if token_id in special:
            continue
        after_zero = tokenizer.decode([zero, token_id], clean_up_tokenization_spaces=False)[1:]
        plain = tokenizer.decode([token_id], clean_up_tokenization_spaces=False)
        if any(ord(c) < 32 for c in after_zero):
            continue  # JSON whitespace in values must use escaped tokens.
        regular.append((token_id, after_zero, len(after_zero) > len(plain)))
    return TokenEnforcerTokenizerData(regular, lambda ids: tokenizer.decode(ids, clean_up_tokenization_spaces=False).rstrip("�"),
                                     tokenizer.eos_token_id, False, len(tokenizer))


def json_prefix_function(tokenizer, tokenizer_data, schema):
    """Retain complete Unicode characters formed from byte-fallback tokens.

    LMFE's free-text shortcut allows byte fragments, but its initial alphabet
    only contains first characters of individually decoded tokens. Register the
    completed suffix before the parser consumes it; enums/regexes stay unchanged.
    """
    from lmformatenforcer import JsonSchemaParser, TokenEnforcer
    enforcer = TokenEnforcer(tokenizer_data, JsonSchemaParser(schema))
    config = enforcer.root_parser.config
    alphabet = set(config.alphabet)
    def allowed_tokens(batch_id, sent):
        ids = sent.tolist()
        tail = tokenizer.decode(ids[-16:], clean_up_tokenization_spaces=False)
        extra = {c for c in tail if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF} - alphabet
        if extra:
            config.alphabet += "".join(sorted(extra))
            alphabet.update(extra)
        return enforcer.get_allowed_tokens(ids).allowed_tokens
    return allowed_tokens


def efficient_sdpa_attention(module, query, key, value, attention_mask, dropout=0.0, scaling=None,
                             is_causal=None, **kwargs):
    """Equivalent GQA expansion for Windows' native memory-efficient SDPA kernel.

    Windows torch 2.10 omits FlashAttention; enable_gqa otherwise selects the
    quadratic-memory math fallback. Keep the original mask/scaling/causality.
    """
    import torch
    from torch.nn.attention import sdpa_kernel, SDPBackend
    if query.shape[1] % key.shape[1]:
        raise ValueError("Query heads must be divisible by key/value heads")
    groups = query.shape[1] // key.shape[1]
    if groups != 1:
        key = key.repeat_interleave(groups, dim=1)
        value = value.repeat_interleave(groups, dim=1)
    causal = getattr(module, "is_causal", True) if is_causal is None else is_causal
    causal = bool(query.shape[2] > 1 and attention_mask is None and causal)
    with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
        output = torch.nn.functional.scaled_dot_product_attention(
            query, key, value, attn_mask=attention_mask, dropout_p=dropout,
            scale=scaling, is_causal=causal)
    return output.transpose(1, 2).contiguous(), None


class TransformersBackend(VisionBackend):
    def __init__(self, config):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig
        self.torch, self.config = torch, config
        torch.set_num_threads(config.get("torch_num_threads", 4))
        if config.get("sequential_weight_loading", True):
            # Transformers 5.3 explicitly supports this memory-bounded loading path.
            os.environ["HF_DEACTIVATE_ASYNC_LOAD"] = "1"
        if config["device"].startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable. Install a matching CUDA PyTorch build, or explicitly set device: cpu.")
        if config.get("gpu_memory_fraction") is not None and config["device"].startswith("cuda"):
            torch.cuda.set_per_process_memory_fraction(config["gpu_memory_fraction"])
        kwargs = {"revision": config["model_revision"], "local_files_only": config["local_files_only"],
                  "cache_dir": config.get("model_cache_dir")}
        self.processor = AutoProcessor.from_pretrained(config["model_name"], **kwargs)
        if config.get("max_image_pixels"):
            self.processor.image_processor.size = {"longest_edge": config["max_image_pixels"],
                                                   "shortest_edge": min(65536, config["max_image_pixels"])}
        model_kwargs = {}
        if config.get("attention_backend") == "sdpa_efficient":
            from transformers import AttentionInterface
            from transformers.masking_utils import AttentionMaskInterface, sdpa_mask
            AttentionInterface.register("materflow_efficient", efficient_sdpa_attention)
            AttentionMaskInterface.register("materflow_efficient", sdpa_mask)
            model_kwargs["attn_implementation"] = "materflow_efficient"
        if config.get("quantization") == "nf4":
            if not torch.cuda.is_available():
                raise RuntimeError("The nf4 run profile requires CUDA")
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                llm_int8_enable_fp32_cpu_offload=config.get("offload_embeddings", False),
                llm_int8_skip_modules=config.get("bnb_skip_modules"))
        device_map = config["device"]
        if config.get("offload_embeddings"):
            device_map = {"model.visual": config["device"], "model.language_model.embed_tokens": "cpu",
                          "model.language_model.layers": config["device"], "model.language_model.norm": config["device"],
                          "model.language_model.rotary_emb": config["device"],
                          "lm_head": config["device"]}
        self.model = AutoModelForImageTextToText.from_pretrained(
            config["model_name"], dtype=torch.bfloat16 if config["device"].startswith("cuda") else torch.float32,
            device_map=device_map, **model_kwargs, **kwargs).eval()
        if config.get("offload_embeddings"):
            # Public Accelerate placement hooks: execute this lookup on CPU instead of
            # copying the entire vocabulary matrix to GPU for every generated token.
            from accelerate.hooks import remove_hook_from_module, add_hook_to_module, AlignDevicesHook
            embedding = self.model.get_input_embeddings()
            remove_hook_from_module(embedding)
            embedding.to("cpu")
            add_hook_to_module(embedding, AlignDevicesHook(execution_device="cpu", io_same_device=True))
        gc.collect()
        if config["device"].startswith("cuda"):
            torch.cuda.empty_cache()
        delta_backend = next((module.chunk_gated_delta_rule.__module__ for module in self.model.modules()
                              if hasattr(module, "chunk_gated_delta_rule")), None)
        self.generation_options = {
            "eos_token_id": self.processor.tokenizer.eos_token_id,
            "pad_token_id": self.processor.tokenizer.pad_token_id or self.processor.tokenizer.eos_token_id,
            "do_sample": config.get("do_sample", False),
            "repetition_penalty": config.get("repetition_penalty", 1.0),
        }
        if config.get("structured_output"):
            self.generation_options["pad_token_id"] = self.processor.tokenizer.eos_token_id
        if self.generation_options["do_sample"]:
            self.generation_options.update(temperature=config.get("temperature", 0.7),
                                           top_p=config.get("top_p", 0.8), top_k=config.get("top_k", 20))
        if config.get("seed") is not None:
            from transformers import set_seed
            set_seed(config["seed"])
        self.version = {"structured_output": config.get("structured_output", False), "generation": {**self.generation_options, "presence_penalty": config.get("presence_penalty", 0.0)}, "seed": config.get("seed"), "model_name": config["model_name"], "requested_revision": config["model_revision"],
                        "resolved_commit": getattr(self.model.config, "_commit_hash", None),
                        "quantization": config.get("quantization"), "bnb_skip_modules": config.get("bnb_skip_modules"),
                        "max_image_pixels": config.get("max_image_pixels"),
                        "max_new_tokens": config["max_new_tokens"], "device_map": getattr(self.model, "hf_device_map", None),
                        "torch_num_threads": torch.get_num_threads(),
                        "sequential_weight_loading": config.get("sequential_weight_loading", True),
                        "gated_delta_backend": delta_backend,
                        "attention_backend": config.get("attention_backend", "sdpa"),
                        "memory_footprint_bytes": self.model.get_memory_footprint()}
        print("Qwen loaded:", self.version, flush=True)
        if config["device"].startswith("cuda"):
            print(f"GPU after loading: allocated={torch.cuda.memory_allocated()}, reserved={torch.cuda.memory_reserved()}", flush=True)

    def _image_content(self, image):
        return {"type": "image", "image": str(Path(image).resolve()) if isinstance(image, (str, Path)) else image}

    def _infer_batch(self, contents, response_schema=None):
        from transformers.generation.streamers import BaseStreamer
        started = time.monotonic()
        class TokenProgress(BaseStreamer):
            def __init__(self):
                self.first = True
                self.tokens = 0
                self.prefill_s = None
            def put(self, value):
                if self.first:
                    self.first = False
                    return
                self.tokens += value.numel()
                if self.prefill_s is None:
                    self.prefill_s = time.monotonic() - started
                    print(f"Qwen first token after {self.prefill_s:.1f}s", flush=True)
                elif self.tokens % 128 == 0:
                    print(f"Qwen generated {self.tokens} tokens in {time.monotonic()-started:.1f}s", flush=True)
            def end(self):
                pass
        progress = TokenProgress()
        self.processor.tokenizer.padding_side = "left"
        inputs = self.processor.apply_chat_template(
            [[{"role": "system", "content": [{"type": "text", "text": "严格按用户给定结构返回紧凑 JSON，不缩进、不换行、不加 Markdown 围栏或解释。同一实物只列一次，描述简短且只含可见属性。"}]},
              {"role": "user", "content": content}] for content in contents],
            tokenize=True, add_generation_prompt=True, padding=True,
            return_dict=True, return_tensors="pt", enable_thinking=False).to(self.config["device"])
        generation_extras = {}
        if response_schema is not None and self.config.get("structured_output"):
            os.environ["LMFE_MAX_CONSECUTIVE_WHITESPACES"] = "1"
            os.environ["LMFE_MAX_JSON_ARRAY_LENGTH"] = "0"
            if not hasattr(self, "_json_tokenizer_data"):
                self._json_tokenizer_data = json_tokenizer_data(self.processor.tokenizer)
            generation_extras["prefix_allowed_tokens_fn"] = json_prefix_function(
                self.processor.tokenizer, self._json_tokenizer_data, response_schema)
        if self.config.get("presence_penalty"):
            from transformers import LogitsProcessor, LogitsProcessorList
            prompt_length = inputs["input_ids"].shape[-1]
            penalty = self.config["presence_penalty"]
            torch = self.torch
            class GeneratedPresencePenalty(LogitsProcessor):
                def __call__(self, input_ids, scores):
                    seen = torch.zeros_like(scores, dtype=torch.bool)
                    seen.scatter_(1, input_ids[:, prompt_length:], True)
                    return scores - penalty * seen.to(scores.dtype)
            generation_extras["logits_processor"] = LogitsProcessorList([GeneratedPresencePenalty()])
        with self.torch.inference_mode():
            generated = self.model.generate(**inputs, **generation_extras, max_new_tokens=self.config["max_new_tokens"], **self.generation_options, streamer=progress)
        trimmed = generated[:, inputs["input_ids"].shape[-1]:]
        self.last_inference = {"input_tokens": int(inputs["input_ids"].shape[-1]), "output_tokens": progress.tokens,
                               "elapsed_s": time.monotonic()-started, "first_token_s": progress.prefill_s}
        return self.processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)

    def _infer(self, content):
        return self._infer_batch([content])[0]

    def infer_images(self, images, prompt, *, response_schema=None):
        prompts = [prompt] * len(images) if isinstance(prompt, str) else list(prompt)
        if len(images) != len(prompts):
            raise ValueError("Each independent image needs one prompt")
        if not images:
            return []
        contents = [[self._image_content(image), {"type": "text", "text": text + "\nOUTPUT MINIFIED JSON ONLY, with no indentation or formatting line breaks. All region_hint_xyxy coordinates must be normalized to [0,1]; use null when unsure. Do not repeat an individual object."}]
                    for image, text in zip(images, prompts)]
        limit = getattr(self, "_image_batch_limit", len(contents))
        if len(contents) <= limit:
            try:
                return self._infer_batch(contents, response_schema=response_schema)
            except self.torch.cuda.OutOfMemoryError:
                if len(contents) < 2 or not self.config.get("allow_batch_fallback", True):
                    raise
                self._image_batch_limit = max(1, len(contents) // 2)
                print(f"CUDA batch OOM: retry identical independent requests in batches of {self._image_batch_limit}", flush=True)
            # Leave the exception block before collecting tensor references in its traceback.
            gc.collect()
            self.torch.cuda.empty_cache()
            limit = self._image_batch_limit
        results = []
        for start in range(0, len(contents), limit):
            results.extend(self._infer_batch(contents[start:start+limit], response_schema=response_schema))
        return results

    def infer_clip(self, frames, timestamps, prompt, *, response_schema=None):
        if len(frames) < 2 or len(frames) != len(timestamps) or any(t is None for t in timestamps):
            raise ValueError("infer_clip needs at least two ordered images with known timestamps")
        if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError("infer_clip timestamps must strictly increase")
        content = []
        for image, time_s in zip(frames, timestamps):
            content.extend([{"type": "text", "text": f"source-relative timestamp: {time_s:.6f}s"}, self._image_content(image)])
        content.append({"type": "text", "text": prompt})
        if response_schema is not None:
            return self._infer_batch([content], response_schema=response_schema)[0]
        return self._infer(content)
