"""Genera reportes de respuestas reales de API con predicciones simuladas."""
import asyncio
import html
import json
from pathlib import Path
import sys
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import httpx
from aiortc import RTCPeerConnection, RTCConfiguration
from av import VideoFrame
from app.main import create_app, Prediction
from app.pipeline import PipelineConfig, WindowBuffer
from app.webrtc import Session
from app.alerts import EventPolicy


async def main():
    output = BACKEND.parent / "docs" / "backend" / "evidencias" / ("EN3_EN4_" + str(uuid4())[:8])
    output.mkdir(parents=True)
    sequence = iter([(5, "Normal", .95), (7.5, "Hurto", .2), (10, "Hurto", .75),
        (12.5, "Hurto", .8), (15, "Hurto", .9), (17.5, "Hurto", .2),
        (20, "Normal", .95), (22.5, "Normal", .95), (25, "Normal", .95),
        (0, "Hurto", .8), (.25, "Hurto", .8), None])
    class Predictor:
        def predict(self, window):
            value = next(sequence)
            if value is None:
                raise RuntimeError("Fallo simulado controlado para verificar interrupción")
            timestamp, clase, confidence = value
            return Prediction(clase=clase, confianza=confidence, timestamp=timestamp, simulated=True)
    app = create_app(Predictor(), transport="webrtc", detection_db=output / "detections.sqlite3",
                     log_db=output / "logs.sqlite3")
    receiver = app.state.video_receiver
    receiver.config = config = PipelineConfig(width=2, height=2, window_size=1)
    captures, timeline = [], []
    def save(name, title, data):
        (output / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        content = html.escape(json.dumps(data, ensure_ascii=False, indent=2))
        page = f'''<!doctype html><meta charset="utf-8"><style>body{{font:16px Arial;margin:30px;background:#f5f7fa;color:#182536}}h1{{font-size:24px}}pre{{background:white;padding:20px;border:1px solid #ccd5df;font:14px Consolas;white-space:pre-wrap}}p{{max-width:1000px}}</style><h1>{html.escape(title)}</h1><p>EN-3 / EN-4 · Evidencia automatizada · Predicciones simuladas (simulated: true). Respuestas capturadas con HTTP ASGI en el backend real; no es una captura de Swagger ni una inferencia VideoMAE.</p><pre>{content}</pre>'''
        (output / (name + ".html")).write_text(page, encoding="utf-8")
        captures.append(name)
    def session(camera):
        item = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id=camera,
                       buffer=WindowBuffer(config), alert_policy=EventPolicy())
        receiver.sessions[item.id] = item
        receiver.spawn(item, receiver.process_windows(item))
        return item
    async def window(item, number, timestamp):
        frame = VideoFrame(2, 2, "rgb24")
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        item.buffer.queue.put_nowait((number, [(frame, timestamp)]))
        await asyncio.wait_for(item.buffer.queue.join(), 10)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evidencia") as client:
            item = session("camara_evidencia")
            for number, timestamp in enumerate([5, 7.5, 10, 12.5, 15, 17.5, 20, 22.5, 25], 1):
                await window(item, number, timestamp)
                assert item.processing_errors == 0
                latest = item.latest_result
                timeline.append(dict(timestamp=timestamp, clase=latest["clase"], confianza=latest["confianza"],
                                     alert=latest["alert"]))
                if number == 2:
                    response = await client.get("/detections/" + latest["detection_id"])
                    save("01_deteccion", "EN-3: datos, consulta por ID y resultado bajo threshold", {
                        "request": "GET /detections/" + latest["detection_id"], "http_status": response.status_code,
                        "body": response.json(), "eventos_confirmados": (await client.get("/alerts")).json()["total"]})
                if number == 4:
                    event_id = latest["alert"]["id"]
                    response = await client.get("/alerts/" + event_id)
                    save("02_evento_activo", "EN-4: evento confirmado, datos y fin pendiente", {
                        "request": "GET /alerts/" + event_id, "http_status": response.status_code, "body": response.json()})
            response = await client.get("/alerts/" + event_id)
            assert response.json()["duracion_segundos"] == 12.5
            save("03_evento_cerrado", "EN-4: mismo evento cerrado tras tres ventanas Normal", {
                "request": "GET /alerts/" + event_id, "http_status": response.status_code, "body": response.json(),
                "numero_de_eventos": (await client.get("/alerts")).json()["total"]})
            save("04_secuencia", "EN-4: secuencia de confirmación y cierre", timeline)
            interrupted = session("camara_interrumpida")
            for number, timestamp in enumerate([0, .25, .5], 1):
                await window(interrupted, number, timestamp)
            response = await client.get("/alerts", params={"camera": "camara_interrumpida"})
            assert response.json()["items"][0]["status"] == "interrupted"
            save("05_interrupcion", "EN-4: fallo de procesamiento interrumpe sin inventar fin", {
                "request": "GET /alerts?camera=camara_interrumpida", "http_status": response.status_code,
                "body": response.json()})
    restarted = create_app(mode="mock", transport="webrtc", detection_db=output / "detections.sqlite3",
                           log_db=output / "logs.sqlite3")
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://evidencia") as client:
            response = await client.get("/alerts/" + event_id)
            assert response.json()["status"] == "closed"
            save("06_persistencia", "EN-4: recuperación después de reiniciar la aplicación", {
                "request": "GET /alerts/" + event_id, "http_status": response.status_code, "body": response.json()})
            response = await client.get("/detections", params={"camera": "camara_evidencia"})
            assert len(response.json()) == 9
            save("07_en3_persistencia", "EN-3: detecciones recuperadas después de reiniciar", {
                "request": "GET /detections?camera=camara_evidencia", "http_status": response.status_code,
                "numero_de_detecciones": len(response.json()), "ejemplo": response.json()[-1]})
    files = sorted(p.name for p in output.glob("*.sqlite3"))
    save("08_almacenamiento", "EN-3 / EN-4: archivos generados por la ejecución", {
        "directorio": str(output), "archivos_sqlite": files,
        "archivos_video": [p.name for p in output.iterdir() if p.suffix.lower() in (".mp4", ".avi", ".webm", ".mkv")],
        "nota": "Complementar con pruebas y revisión del código: esta lista no prueba por sí sola todos los caminos de ejecución."})
    (output / "index.html").write_text('<meta charset="utf-8"><h1>Evidencias EN-3 / EN-4</h1>' + ''.join(
        f'<p><a href="{name}.html">{name}</a></p>' for name in captures), encoding="utf-8")
    print(output)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
