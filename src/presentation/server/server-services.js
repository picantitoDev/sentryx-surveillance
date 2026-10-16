import { createCameraService } from '#application/cameras/camera-service.js';
import { createMonitoringService } from '#application/monitoring/monitoring-service.js';
import { createServiceLogService } from '#application/logs/service-log-service.js';
import { callInference } from '#infrastructure/inference/fastapi-client.js';
import { getRegisteredCamera, getRegisteredCameras, mapCamera } from '#infrastructure/cameras/fastapi-camera-registry.js';

const cameraRegistry = {
  list: getRegisteredCameras,
  get: getRegisteredCamera,
  map: mapCamera
};

export const cameraService = createCameraService({ inferenceClient: callInference, cameraRegistry });
export const monitoringService = createMonitoringService({ inferenceClient: callInference, cameraRegistry });
export const serviceLogService = createServiceLogService({ inferenceClient: callInference });
