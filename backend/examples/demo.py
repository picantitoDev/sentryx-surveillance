r"""
.\.venv\Scripts\python.exe backend/examples/demo.py
Agregar --auto para ejecutar sin pausas. Nunca escribe en la base de operación.
"""
import argparse
import asyncio
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
from av import VideoFrame
from aiortc import RTCPeerConnection, RTCConfiguration
from app.main import create_app, Prediction
from app.pipeline import PipelineConfig, WindowBuffer
from app.webrtc import Session
from app.alerts import EventPolicy


WINDOWS = [(5, "Normal", .95), (10, "Hurto", .75), (12.5, "Hurto", .8),
           (15, "Hurto", .9), (17.5, "Hurto", .2), (20, "Normal", .95),
           (22.5, "Normal", .95), (25, "Normal", .95)]


class DemoPredictor:
    def __init__(self):
        self.values = iter(WINDOWS)

    def predict(self, window):
        timestamp, clase, confidence = next(self.values)
        assert timestamp == window.timestamp
        return Prediction(clase=clase, confianza=confidence, timestamp=timestamp, simulated=True)


async def demo(auto):
    app = create_app(DemoPredictor(), transport="webrtc",
                     detection_db=BACKEND / "data" / "daily" / "detections.sqlite3",
                     log_db=BACKEND / "data" / "daily" / "inference_logs.sqlite3")
    receiver = app.state.video_receiver
    config = PipelineConfig(width=2, height=2, window_size=1)
    receiver.config = config
    session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="camara_daily",
                      buffer=WindowBuffer(config), alert_policy=EventPolicy())
    print("DEMO SIMULADA: prueba confirmación, intervalos, persistencia y APIs; no ejecuta VideoMAE.")
    print("Se usan bases separadas en backend/data/daily. Cada ejecución agrega una sesión nueva.\n")
    async with app.router.lifespan_context(app):
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        await receiver.log_event("source_connected", "Fuente simulada de daily iniciada", session=session)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as client:
            print("VIDEO(s)  CLASE     CONFIANZA  EVENTO")
            event_id = None
            for number, (timestamp, clase, confidence) in enumerate(WINDOWS, 1):
                frame = VideoFrame(2, 2, "rgb24")
                frame.planes[0].update(bytes(frame.planes[0].buffer_size))
                # Ventanas ya preparadas: permite avanzar el tiempo del video sin esperar 25 segundos.
                session.buffer.queue.put_nowait((number, [(frame, timestamp)]))
                await asyncio.wait_for(session.buffer.queue.join(), 10)
                assert session.processing_errors == 0, session.last_error
                state = session.latest_result["alert"]
                label = "ABIERTO" if state["started"] else "CERRADO" if state["ended"] else \
                        "EN CURSO" if state["active"] else "CANDIDATO" if state["candidate"] else "SIN EVENTO"
                print(f"{timestamp:8.1f}  {clase:8}  {confidence:9.2f}  {label}")
                if state["started"]:
                    event_id = state["id"]
                    record = (await client.get(f"/alerts/{event_id}")).json()
                    assert record["status"] == "active" and record["timestamp_fin"] is None
                    print(f"  ID: {event_id}\n  Inicio: {record['timestamp_inicio']} s; fin: null")
                    if not auto:
                        await asyncio.to_thread(input, "  Evento abierto. Enter para continuar hasta Normal... ")
            record = (await client.get(f"/alerts/{event_id}")).json()
            assert record["status"] == "closed"
            assert record["timestamp_inicio"] == 12.5 and record["timestamp_fin"] == 25
            assert record["duracion_segundos"] == 12.5
            print(f"\nMISMO EVENTO: {event_id}")
            print("Inicio: 12.5 s | Fin: 25 s | Duración: 12.5 s | Estado: closed")
            for key in ("detection_id", "end_detection_id"):
                detection_response = await client.get(f"/detections/{record[key]}")
                assert detection_response.status_code == 200
                detection = detection_response.json()
                print(f"{key}: {detection['clase']} @ {detection['timestamp']} s, simulated={detection['simulated']}")
            logs = (await client.get("/inference-logs", params={"camera": "camara_daily"})).json()
            assert logs["total"] > 0
    # Un nuevo lifespan recupera el historial de la misma base.
    restarted = create_app(mode="mock", transport="webrtc", detection_db=app.state.detection_store.path,
                           log_db=app.state.log_store.path)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://demo") as client:
            recovered = await client.get(f"/alerts/{event_id}")
            assert recovered.status_code == 200 and recovered.json()["status"] == "closed"
            print("PERSISTENCIA VERIFICADA: el mismo evento cerrado se recupera después de reiniciar la aplicación.")
    print(f"\nPara consultar desde Swagger: GET /alerts/{event_id}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto", action="store_true", help="Ejecutar sin esperar Enter")
    asyncio.run(demo(parser.parse_args().auto))
