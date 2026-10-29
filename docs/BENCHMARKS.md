# GPU benchmark results

## Validated environment

| Component | Value |
|---|---|
| OS | Windows 10 22H2 (10.0.19045) |
| CPU | AMD Ryzen 5 7500F, 6 cores / 12 threads |
| RAM | 32 GB |
| GPU | AMD Radeon RX 7800 XT |
| GPU backend | DirectML through `torch-directml 0.2.5.dev240914` |
| OCR backend | `onnxruntime-directml 1.20.1`, `DmlExecutionProvider` |
| YOLO | OmniParser v2 `icon_detect.pt`, YOLO11m, 20,053,779 parameters |
| OCR | RapidOCR 1.2.3 / PP-OCR ONNX |
| Input size | `imgsz=1280` |
| Thresholds | icon `0.18`, OCR `0.40` |

The CPU and GPU runs used the same model file, preprocessing, thresholds and fusion code.
Model weights remained resident across requests.

## Workload

The dataset contains 12 unique Arknights navigation screenshots at 2558 x 1377 pixels.
The two JSON-response benchmarks executed three full passes per backend after a discarded
warm-up request: 36 measured requests each. The annotated-PNG benchmark executed one full
pass per backend: 12 measured requests each.

Default JSON requests do not render an annotation. The engine renders the PNG only when
`response_format=png` or `include=annotated_image`/`include=all` requests it.

## Latency

### JSON response

| Stage | CPU mean | DirectML mean | GPU speed-up |
|---|---:|---:|---:|
| Decode and normalize | 0.2675 s | 0.2639 s | 1.01x |
| YOLO icon detection | 0.4190 s | 0.0728 s | 5.76x |
| RapidOCR | 1.0051 s | 0.5356 s | 1.88x |
| Fusion | 0.0077 s | 0.0075 s | 1.03x |
| End-to-end total | **1.7019 s** | **0.8814 s** | **1.93x** |
| Median end-to-end | 1.6168 s | 0.8248 s | 1.96x |

Mean end-to-end latency fell by 48.21%. The GPU gain is bounded by CPU-side image decoding,
OCR preprocessing, fusion and response construction. YOLO itself was 5.76x faster and OCR
was 1.88x faster. DirectML latency varies with GPU clock state and desktop graphics load;
this table reports one complete 36-request run rather than a synthetic microbenchmark.

### Annotated PNG response

| Backend | Mean end-to-end |
|---|---:|
| CPU | 2.3690 s |
| DirectML | 1.2069 s |

The annotated path was 1.96x faster and reduced latency by 49.05%. Annotation rendering
remains on the CPU and consumes the largest share of the remaining GPU-path latency.

## Output consistency

CPU output was used as the reference because both backends run the same model and pipeline.

| Output | CPU count | DirectML count | Matched | Missing | Extra | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Clickable controls | 449 | 449 | 449 | 0 | 0 | 1.000 |
| Fused elements | 450 | 450 | 450 | 0 | 0 | 1.000 |
| OCR text blocks | 429 | 429 | 429 | 0 | 0 | 1.000 |

For one-to-one box matches at IoU >= 0.5:

- Control text and semantic role accuracy: **100%**
- OCR exact-text accuracy: **100%**
- Mean control/element/text box IoU: **1.000**
- Mean and maximum safe-click-point displacement: **0 px**

The raw YOLO comparison was also checked before fusion on the same frames: all 276
detections matched at IoU >= 0.5, with mean IoU `0.999999`, maximum rounded box difference
of `0.001 px`, and maximum confidence difference below `0.000004`.

These metrics measure parity with the CPU backend. They are not an absolute accuracy
measurement against a separately human-labeled object-detection test set. The repository
does not include that dataset or its test screenshots by design.

Machine-readable results are stored in
[`benchmarks/amd-rx7800xt-directml-v0.2.0.json`](benchmarks/amd-rx7800xt-directml-v0.2.0.json).

## Reproduce

Install the DirectML environment and start with matching model settings:

```powershell
.\scripts\setup-windows-directml.ps1
$env:PYTHONPATH="$PWD\src"
```

Run CPU and DirectML benchmarks into the ignored `artifacts` directory:

```powershell
python scripts\benchmark.py --images "C:\frames\*.png" `
  --device cpu --ocr-device cpu --repeat 3 `
  --output artifacts\gpu-benchmark\cpu.json

python scripts\benchmark.py --images "C:\frames\*.png" `
  --device directml --ocr-device directml --repeat 3 `
  --output artifacts\gpu-benchmark\directml.json

python scripts\compare_benchmarks.py `
  artifacts\gpu-benchmark\cpu.json `
  artifacts\gpu-benchmark\directml.json `
  --output artifacts\gpu-benchmark\comparison.json
```

Add `--annotated` to both benchmark commands to measure the PNG-rendering path. Use a
consistent input set, model file and threshold for both backends.

## CUDA image packaging validation

The `Dockerfile.cuda` image was built locally from
`pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime`, PyTorch `2.4.1+cu124`,
`onnxruntime-gpu 1.20.2`, RapidOCR and the verified YOLO model.

A read-only, capability-dropped, non-root container was then started with
`LABEL_CONTROLLERS_DEVICE=cpu` because the validation host has no NVIDIA GPU. The
CUDA image's CPU fallback passed:

- health and readiness checks;
- `CPUExecutionProvider` OCR initialization;
- authenticated analysis returning 43 controls and 39 OCR blocks;
- unauthenticated rejection with HTTP `401`.

This validates image construction and application packaging, not CUDA kernel execution.
CUDA throughput and provider activation must be measured on the target NVIDIA host with
the same benchmark scripts.

## OCR recognition batch tuning

The provider-aware OCR default keeps RapidOCR's conservative batch size 6 on CPU and uses
batch size 12 on GPU. The following comparison used the same 12 Arknights screenshots,
DirectML, `PP-OCRv3 Mobile`, full-resolution text detection, angle classification enabled
and three measured passes per configuration. Each result therefore represents 36 requests.

| Stage | Batch 6 | Batch 12 | Change |
|---|---:|---:|---:|
| Decode and normalize | 0.2672 s | 0.2683 s | unchanged |
| YOLO icon detection | 0.0749 s | 0.0775 s | unchanged |
| RapidOCR | 0.5496 s | 0.4781 s | **13.0% lower** |
| Fusion | 0.0080 s | 0.0081 s | unchanged |
| End-to-end mean | **0.9013 s** | **0.8337 s** | **1.081x faster, 7.5% lower latency** |

Unlike a model substitution, this changes output because RapidOCR 1.2.3 pads every input
in a recognition batch to the widest item in that batch. Batch boundaries therefore affect
the recognition tensor width. Relative to batch 6, batch 12 produced:

| Output | Batch 6 | Batch 12 | Matched | Missing | Extra | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Clickable controls | 449 | 447 | 442 | 7 | 5 | 0.9866 |
| Fused elements | 450 | 448 | 443 | 7 | 5 | 0.9866 |
| OCR text blocks | 429 | 427 | 425 | 4 | 2 | 0.9930 |

For OCR boxes matched at IoU >= 0.5, exact-text accuracy was 96.71%, while box IoU was
1.000. The difference is therefore caused by recognition/padding behavior rather than text
detection. Set `LABEL_CONTROLLERS_OCR_REC_BATCH_NUM=6` to retain the original output path.
This is a measured speed/accuracy trade-off, not a byte-for-byte parity result.

Reproduce:

```powershell
python scripts\benchmark.py --images "C:\frames\*.png" `
  --device directml --ocr-device directml --ocr-rec-batch-num 6 `
  --ocr-use-angle-cls --repeat 3 --output artifacts\ocr-batch12\benchmark-b6.json

python scripts\benchmark.py --images "C:\frames\*.png" `
  --device directml --ocr-device directml --ocr-rec-batch-num 12 `
  --ocr-use-angle-cls --repeat 3 --output artifacts\ocr-batch12\benchmark-b12.json

python scripts\compare_benchmarks.py `
  artifacts\ocr-batch12\benchmark-b6.json `
  artifacts\ocr-batch12\benchmark-b12.json `
  --output artifacts\ocr-batch12\comparison.json
```

Machine-readable summary:
[`benchmarks/amd-rx7800xt-directml-ocr-batch-v0.4.0.json`](benchmarks/amd-rx7800xt-directml-ocr-batch-v0.4.0.json).

## OCR preprocessing: angle classifier and decoded-image reuse

The v0.5.0 OCR path applies two output-preserving optimizations before recognition:

1. RapidOCR's 180-degree angle classifier is disabled by default for upright UI text.
2. The RGB image produced during Pillow normalization is passed directly to RapidOCR and
   reused for fusion sizing, instead of decoding the canonical PNG a second time.

The comparison used the same 12 screenshots and three measured passes per backend
(36 requests per configuration). DirectML used batch 12 and CPU used batch 6.

| Stage | DirectML baseline | DirectML optimized | Change |
|---|---:|---:|---:|
| Decode and normalize | 0.2562 s | 0.2620 s | +2.3% (array materialization moved here) |
| YOLO icon detection | 0.0717 s | 0.0724 s | unchanged |
| RapidOCR | 0.4259 s | 0.2630 s | **38.3% lower** |
| Fusion | 0.0072 s | 0.0068 s | unchanged |
| End-to-end mean | **0.7625 s** | **0.6056 s** | **1.259x faster, 20.6% lower latency** |

On CPU, RapidOCR fell from 0.9038 s to 0.8328 s (7.9%) and end-to-end latency from
1.5644 s to 1.4956 s (4.4%). YOLO dominates the CPU path, so the end-to-end effect is
smaller there.

Output parity against the same backend and batch size was exact on the corpus:

| Backend | Clickable controls | OCR text blocks | Missing | Extra | F1 |
|---|---:|---:|---:|---:|---:|
| DirectML batch 12 | 447 -> 447 | 427 -> 427 | 0 | 0 | 1.000 |
| CPU batch 6 | 449 -> 449 | 429 -> 429 | 0 | 0 | 1.000 |

All matched boxes retained exact text, role and click coordinates. The angle classifier only
predicts upside-down orientation; RapidOCR's existing tall-crop rotation remains active.
If the target workload includes upside-down text, set
`LABEL_CONTROLLERS_OCR_USE_ANGLE_CLS=true` to restore the classifier.

The effective settings are reported in `metadata.models.ocr` and `metadata.raw_ocr` as
`use_angle_cls` and `decoded_image_reuse`.

Reproduce the optimized run for DirectML:

```powershell
python scripts\benchmark.py --images "C:\frames\*.png" `
  --device directml --ocr-device directml --ocr-rec-batch-num 12 `
  --no-ocr-use-angle-cls --repeat 3 `
  --output artifacts\ocr-p0\optimized-directml.json
```

For the baseline, check out the pre-optimization commit `e50f933` in a separate worktree,
set `PYTHONPATH` to that worktree's `src` directory, and run the same command without
`--no-ocr-use-angle-cls`. For CPU, use `--device cpu --ocr-device cpu --ocr-rec-batch-num 6`.
Compare the files with `scripts/compare_benchmarks.py`; the A/B measurements above used
that script with IoU >= 0.5.

Machine-readable summary:
[`benchmarks/amd-rx7800xt-ocr-preprocessing-v0.5.0.json`](benchmarks/amd-rx7800xt-ocr-preprocessing-v0.5.0.json).

## Interpretation

- DirectML provides a meaningful speed-up on the tested AMD GPU while preserving outputs
  when OCR batch 6 is selected; the default GPU batch 12 is a measured speed/accuracy trade-off.
- Disabling the redundant angle classifier and reusing the normalized RGB array preserved
  every output on the 12-image corpus while lowering DirectML end-to-end latency by 20.6%.
- In the v0.2.0 run OCR accounted for about 61% of GPU-path request time and YOLO about 8%;
  OCR batching/provider behavior remains the highest-leverage optimization area.
- Annotation rendering is CPU-bound. Rendering only on request already avoids paying that
  cost for the common controls/elements/texts JSON response.
- CUDA timing will differ based on GPU generation, driver and ONNX Runtime CUDA EP.
  Re-run these scripts on the target NVIDIA host instead of extrapolating DirectML results.
