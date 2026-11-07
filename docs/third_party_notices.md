# Third-party notices

LM Format Enforcer tokenizer integration in materflow/backends/transformers_backend.py adapts the token-decoding algorithm from https://github.com/noamgat/lm-format-enforcer (version 0.11.3). The JSON grammar engine itself is used as an installed dependency.

MIT License

Copyright (c) 2023 Noam Gat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


## V4.1 isolated clock OCR

RapidOCR-ONNXRuntime 1.4.4 (Apache-2.0, https://github.com/RapidAI/RapidOCR), ONNX Runtime 1.23.2 (MIT, https://onnxruntime.ai) and opencv-python 4.12.0.88 (Apache-2.0) are installed in a separate CPU environment. Their bundled license files remain in that environment. The pretrained OCR ONNX filenames and SHA256 values are recorded in each clock_raw.json; raw readings are preserved. No OCR implementation or model weights are copied into this repository.
