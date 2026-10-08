"""Measure the installed voice transformer's CPU path before choosing settings."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "data/models"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def main():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    results = []
    destination = ROOT / "data/models/cpu-benchmark.json"

    def record(value):
        results.append(value)
        destination.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(value), flush=True)

    torch.set_num_threads(4)
    model = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
    transformer = model.t3.tfmr
    torch.manual_seed(17)
    context = torch.randn(2, 128, model.t3.dim)
    token = torch.randn(2, 1, model.t3.dim)

    @torch.inference_mode()
    def measure(threads, quantized):
        torch.set_num_threads(threads)
        past = transformer(
            inputs_embeds=context,
            use_cache=True,
            output_attentions=True,
            output_hidden_states=True,
            return_dict=True,
        ).past_key_values
        # Keep the growing KV cache, as the installed autoregressive decoder does.
        samples = []
        for index in range(12):
            started = time.perf_counter()
            output = transformer(
                inputs_embeds=token,
                past_key_values=past,
                use_cache=True,
                output_attentions=True,
                output_hidden_states=True,
                return_dict=True,
            )
            past = output.past_key_values
            if index > 1:
                samples.append(time.perf_counter() - started)
        average = sum(samples) / len(samples)
        record(
            {
                "threads": threads,
                "quantized": quantized,
                "seconds_per_step": average,
                "steps_per_second": 1 / average,
            }
        )

    for threads in (1, 2, 4, 6):
        measure(threads, False)
    torch.set_num_threads(4)
    transformer = torch.ao.quantization.quantize_dynamic(
        transformer, {torch.nn.Linear}, dtype=torch.qint8, inplace=True
    )
    for threads in (1, 2, 4, 6):
        measure(threads, True)
    fastest = min(results, key=lambda result: result["seconds_per_step"])
    baseline = next(
        result
        for result in results
        if result["threads"] == 6 and not result["quantized"]
    )
    profile = {
        "threads": fastest["threads"],
        "quantized": fastest["quantized"],
        "transformer_speedup": baseline["seconds_per_step"]
        / fastest["seconds_per_step"],
        "benchmark": str(destination),
    }
    (ROOT / "data/models/cpu-profile.json").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    print(json.dumps(profile), flush=True)


if __name__ == "__main__":
    main()
