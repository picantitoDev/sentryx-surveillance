# VideoMAE Crime5 Continuous

Modelo ONNX para detección y clasificación continua de acciones.

## Clases

0. normal
1. hurto
2. robo
3. agresion_fisica
4. vandalismo

## Instalación

python -m pip install -r requirements.txt

## Ejecución

python infer_onnx.py --video "RUTA_DEL_VIDEO.mp4" --provider cpu

Providers disponibles: auto, cpu y cuda.

## Entrada y salida

Entrada: tensor float32 [1, 3, 16, 224, 224], disposición NCTHW.
Salida: cinco logits en el orden indicado anteriormente.

El script realiza el muestreo, preprocesamiento, softmax y control temporal de alertas.

## Política temporal

- Suavizado: 4 ventanas
- Umbral: 0.40
- Confirmación: 2 ventanas
- Liberación: 3 ventanas

IMPORTANTE: videomae_crime5_continuous.onnx y
videomae_crime5_continuous.onnx.data deben permanecer juntos.


## Obtener los pesos

Los pesos no están incluidos en Git. Solicitar al responsable del modelo los
archivos `videomae_crime5_continuous.onnx` y
`videomae_crime5_continuous.onnx.data`, y colocarlos juntos en esta carpeta.
La ubicación compartida de descarga queda por acordar con el equipo.
`SHA256SUMS.txt` contiene los hashes esperados. No modificar los metadatos para
acomodar otra versión del modelo sin validar su compatibilidad.

En PowerShell, desde la raíz del repositorio:

```powershell
Get-FileHash modelo/videomae_crime5_continuous.onnx -Algorithm SHA256
Get-FileHash modelo/videomae_crime5_continuous.onnx.data -Algorithm SHA256
```

El backend utiliza su propia política de eventos confirmados para EN-4;
la política descrita arriba corresponde al script independiente de inferencia.
Instalación y ejecución del servicio: [backend/README.md](../backend/README.md).
