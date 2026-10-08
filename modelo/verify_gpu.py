"""Prueba acotada: clip real, salidas finitas y evidencia de ejecución CUDA."""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from infer_onnx import ROOT, create_session, prepare_clip, softmax


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", default=str(ROOT / "gpu_validation.json"))
    args = parser.parse_args()
    metadata = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    temporal = metadata["temporal_sampling"]
    cap = cv2.VideoCapture(str(Path(args.video).resolve()))
    frames = []
    try:
        if not cap.isOpened():
            raise RuntimeError("No se pudo abrir el video")
        fps = cap.get(cv2.CAP_PROP_FPS)
        for index in range(temporal["required_frame_span"]):
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("El video no contiene una ventana completa")
            if index % temporal["sampling_rate"] == 0:
                frames.append(frame)
    finally:
        cap.release()
    preprocessing = metadata["preprocessing"]
    tensor = prepare_clip(frames, preprocessing["crop_size"],
                          preprocessing["mean"], preprocessing["std"])
    if list(tensor.shape) != metadata["input"]["shape"]:
        raise RuntimeError(f"Forma de entrada incorrecta: {tensor.shape}")
    session = create_session(ROOT / metadata["onnx_file"], "cuda")
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].name != metadata["input"]["name"]:
        raise RuntimeError("El contrato ONNX no coincide con metadata.json")
    if inputs[0].shape != metadata["input"]["shape"] or inputs[0].type != "tensor(float)":
        raise RuntimeError("Tipo o dimensiones ONNX incompatibles")
    # Warm-up; luego tres mediciones del mismo clip para verificar estabilidad.
    session.run(None, {inputs[0].name: tensor})
    durations = []
    for _ in range(3):
        start = time.perf_counter()
        logits = session.run(None, {inputs[0].name: tensor})[0]
        durations.append((time.perf_counter() - start) * 1000)
        if logits.shape != (1, 5) or not np.isfinite(logits).all():
            raise RuntimeError("Salida inválida")
    probabilities = softmax(logits[0])
    if not np.isclose(probabilities.sum(), 1):
        raise RuntimeError("Probabilidades inválidas")
    # Perfil separado: comprueba nodos CUDA, sin contaminar las mediciones.
    del session
    options = ort.SessionOptions()
    options.enable_profiling = True
    options.profile_file_prefix = str(ROOT / "gpu_profile")
    profiled = ort.InferenceSession(str(ROOT / metadata["onnx_file"]),
                                   sess_options=options,
                                   providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    profiled.disable_fallback()
    profiled.run(None, {inputs[0].name: tensor})
    profile_path = Path(profiled.end_profiling())
    events = json.loads(profile_path.read_text(encoding="utf-8"))
    provider_nodes = {}
    for event in events:
        provider = event.get("args", {}).get("provider")
        if provider:
            provider_nodes[provider] = provider_nodes.get(provider, 0) + 1
    if not provider_nodes.get("CUDAExecutionProvider"):
        raise RuntimeError("No hay evidencia de nodos ejecutados en GPU")
    result = {
        "onnxruntime": ort.__version__, "providers": profiled.get_providers(),
        "video": str(Path(args.video).resolve()), "fps": fps,
        "input_shape": list(tensor.shape), "input_dtype": str(tensor.dtype),
        "inference_ms": durations, "mean_inference_ms": float(np.mean(durations)),
        "probabilities": dict(zip(metadata["class_names"], probabilities.tolist())),
        "class": metadata["class_names"][int(np.argmax(probabilities))],
        "profile_nodes_by_provider": provider_nodes,
        "profile": str(profile_path),
        "scope": "Compatibilidad de ejecución; no valida exactitud ni latencia cámara-pantalla.",
    }
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
