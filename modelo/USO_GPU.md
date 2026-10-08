# VideoMAE en GPU

Desde la raíz del repositorio, crear `.venv`, instalar
`backend/requirements-gpu.txt` y obtener ambos pesos según [README.md](README.md).
Se requiere GPU NVIDIA y controlador compatible con las dependencias CUDA/cuDNN.

```powershell
.\.venv\Scripts\python.exe modelo/test_gpu_provider.py
.\.venv\Scripts\python.exe modelo/verify_gpu.py --video C:/ruta/a/video-de-prueba.mp4
.\.venv\Scripts\python.exe modelo/infer_onnx.py --video C:/ruta/a/video-de-prueba.mp4 --provider cuda
```

Los reportes GPU generados son diagnósticos locales excluidos de Git.
El backend se inicia con `./backend/start_gpu.ps1`; verificar en `/health`
`model_loaded: true`, `gpu_active: true` y `CUDAExecutionProvider`.
El script exige CUDA y no cambia automáticamente a CPU ni al simulador.
No instalar onnxruntime y onnxruntime-gpu simultáneamente en el mismo entorno.

El backend usa los mismos metadatos y helpers de preprocesamiento que el modelo.
La política EN-4 de eventos por sesión está documentada en el
[contrato de alertas](../docs/backend/CONTRATO_ALERTAS_FRONTEND.md).
El contrato de resultados está en
[RESULTADOS_VIDEOMAE.md](../docs/backend/RESULTADOS_VIDEOMAE.md).
