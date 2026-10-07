import { env } from '$env/dynamic/public';
import { createApiClient } from './api';
import { createSocketClient, type SocketOptions } from './websocket';

// Leer estas variables no abre conexiones ni inicia peticiones.
export function getBackendConfig() {
	return {
		apiBaseUrl: env.PUBLIC_API_BASE_URL?.trim() ?? '',
		wsUrl: env.PUBLIC_WS_URL?.trim() ?? ''
	};
}
export function getApiClient() {
	return createApiClient(getBackendConfig().apiBaseUrl);
}
export function getSocketClient(options: SocketOptions) {
	return createSocketClient(getBackendConfig().wsUrl, options);
}
