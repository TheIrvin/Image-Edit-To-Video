"""OpenVINO adapters for the official Spanish Melo/OpenVoice inference graphs."""
from pathlib import Path
import json
import time

import numpy as np
import torch
import openvino as ov


class MeloGraph(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, x, lengths, speaker, tones, languages, bert, ja_bert,
                noise, length, noise_w, ratio):
        return self.model.infer(x, lengths, speaker, tones, languages, bert, ja_bert,
            noise_scale=noise, length_scale=length, noise_scale_w=noise_w, sdp_ratio=ratio)[0]


class ConverterGraph(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, spec, lengths, source, target, tau):
        return self.model.voice_conversion(spec, lengths, source, target, tau=tau)[0]


def prepare(model, converter, folder):
    from melo import utils
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    bert, ja, phones, tones, languages = utils.get_text_for_tts_infer(
        "Nico abrió la puerta y miró hacia el puente.", "ES", model.hps, "cpu", model.symbol_to_id)
    examples = (
        (phones[None], torch.tensor([len(phones)]), torch.tensor([model.hps.data.spk2id['ES']]),
         tones[None], languages[None], bert[None], ja[None],
         torch.tensor(0.6), torch.tensor(1.), torch.tensor(0.8), torch.tensor(0.2)),
        (torch.randn(1, converter.hps.data.filter_length // 2 + 1, 240),
         torch.tensor([240]), torch.randn(1, 256, 1), torch.randn(1, 256, 1), torch.tensor(0.3)),
    )
    for name, graph, example in zip(('melo-es', 'converter'),
                                    (MeloGraph(model.model), ConverterGraph(converter.model)), examples):
        path = folder / f'{name}.xml'
        if not path.exists():
            print(f'Convirtiendo {name} a OpenVINO…', flush=True)
            graph.eval()
            with torch.no_grad():
                converted = ov.convert_model(graph, example_input=example)
            ov.save_model(converted, path, compress_to_fp16=True)
    return folder


def activate(model, converter, folder, device='CPU', allow_fallback=True):
    core = ov.Core()
    config = {'PERFORMANCE_HINT': 'LATENCY', 'CACHE_DIR': str(Path(folder) / 'compiled')}
    if device == 'CPU':
        config.update(INFERENCE_NUM_THREADS=4)
    tts = core.compile_model(str(Path(folder) / 'melo-es.xml'), device, config)
    vc = core.compile_model(str(Path(folder) / 'converter.xml'), device, config)
    original_infer = model.model.infer
    original_convert = converter.model.voice_conversion
    failed = set()

    def inputs(values):
        return [v.detach().cpu().numpy() if isinstance(v, torch.Tensor)
                else np.array(v, dtype=np.float32) for v in values]

    def infer(x, x_lengths, sid, tone, language, bert, ja_bert, noise_scale=0.667,
              length_scale=1., noise_scale_w=0.8, max_len=None, sdp_ratio=0., **kwargs):
        try:
            if 'tts' in failed:
                raise RuntimeError('OpenVINO TTS deshabilitado en este proceso')
            output = tts(inputs((x, x_lengths, sid, tone, language, bert, ja_bert,
                                 noise_scale, length_scale, noise_scale_w, sdp_ratio)))[0]
            if not np.isfinite(output).all() or output.size < 100:
                raise ValueError('OpenVINO produjo audio inválido')
            return (torch.from_numpy(output.copy()),)
        except Exception as error:
            if not allow_fallback:
                raise
            if 'tts' not in failed:
                print(f'OpenVINO TTS: usando alternativa PyTorch: {error}', flush=True)
            failed.add('tts')
            return original_infer(x, x_lengths, sid, tone, language, bert, ja_bert,
                noise_scale=noise_scale, length_scale=length_scale, noise_scale_w=noise_scale_w,
                max_len=max_len, sdp_ratio=sdp_ratio, **kwargs)

    def voice_conversion(y, y_lengths, sid_src, sid_tgt, tau=0.3):
        try:
            if 'vc' in failed:
                raise RuntimeError('OpenVINO conversión deshabilitada en este proceso')
            output = vc(inputs((y, y_lengths, sid_src, sid_tgt, tau)))[0]
            if not np.isfinite(output).all() or output.size < 100:
                raise ValueError('OpenVINO produjo audio inválido')
            return (torch.from_numpy(output.copy()),)
        except Exception as error:
            if not allow_fallback:
                raise
            if 'vc' not in failed:
                print(f'OpenVINO conversión: usando alternativa PyTorch: {error}', flush=True)
            failed.add('vc')
            return original_convert(y, y_lengths, sid_src, sid_tgt, tau=tau)

    model.model.infer = infer
    converter.model.voice_conversion = voice_conversion
    return core.available_devices


def benchmark(model, converter, source, target, request, folder):
    import soundfile as sf
    folder = Path(folder)
    results = []
    original_infer, original_convert = model.model.infer, converter.model.voice_conversion
    devices = ['PyTorch', 'CPU']
    # La primera ejecución con formas dinámicas en Iris Xe puede compilar
    # kernels durante minutos. Sólo medir GPU cuando se solicite explícitamente.
    if request.get('benchmark_gpu') and 'GPU' in ov.Core().available_devices:
        devices.append('GPU')
    for device in devices:
        try:
            model.model.infer, converter.model.voice_conversion = original_infer, original_convert
            compile_start = time.monotonic()
            if device != 'PyTorch':
                activate(model, converter, folder, device, allow_fallback=False)
            compile_seconds = time.monotonic() - compile_start
            # Warm up kernels on a tiny phrase before timing real scenes.
            model.tts_to_file('Hola.', model.hps.data.spk2id['ES'], quiet=True)
            timings = []
            for index, item in enumerate(request['items']):
                torch.manual_seed(17)
                base = folder / f'benchmark-{device}-{index}-base.wav'
                before = time.monotonic()
                model.tts_to_file(item['text'], model.hps.data.spk2id['ES'], str(base), quiet=True)
                base_seconds = time.monotonic() - before
                before = time.monotonic()
                waveform = converter.convert(str(base), src_se=source, tgt_se=target, message='ImgV')
                clone_seconds = time.monotonic() - before
                if not np.isfinite(waveform).all() or not len(waveform) or np.sqrt(np.mean(waveform**2)) < 1e-5:
                    raise ValueError('Audio inválido o vacío')
                sf.write(folder / f'benchmark-{device}-{index}.wav', waveform, converter.hps.data.sampling_rate)
                timings.append({'base_seconds':base_seconds,'clone_seconds':clone_seconds,
                                'audio_seconds':len(waveform)/converter.hps.data.sampling_rate})
                print(json.dumps({'device':device,'scene':index,**timings[-1]}), flush=True)
            results.append({'device':device,'compile_seconds':compile_seconds,'scenes':timings,
                            'total_seconds':sum(x['base_seconds']+x['clone_seconds'] for x in timings)})
        except Exception as error:
            print(f'Benchmark {device}: {error}', flush=True)
            results.append({'device':device,'error':str(error)})
        (folder / 'benchmark.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    valid = [r for r in results if 'error' not in r]
    baseline = next((r for r in valid if r['device']=='PyTorch'), None)
    if baseline:
        # Detectar trazas que no generalizan a textos de distintas longitudes.
        valid = [r for r in valid if all(
            0.6 <= sample['audio_seconds'] / base['audio_seconds'] <= 1.6
            for sample, base in zip(r['scenes'], baseline['scenes']))]
    if not valid:
        raise ValueError('Ningún motor produjo un resultado válido.')
    fastest = min(valid, key=lambda r:r['total_seconds'])
    profile = {'enabled':fastest['device']!='PyTorch', 'device':fastest['device'],
               'benchmark_verified':True,'speedup':baseline['total_seconds']/fastest['total_seconds'] if baseline else None}
    temporary = folder / 'profile.tmp'
    temporary.write_text(json.dumps(profile, indent=2), encoding='utf-8')
    temporary.replace(folder / 'profile.json')
