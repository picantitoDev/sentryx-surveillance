export type SocketStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error';
export type SocketOptions = {
	onMessage: (message: unknown) => void;
	onStatus?: (status: SocketStatus) => void;
	onError?: (error: Error) => void;
	protocols?: string | string[];
};

/** WebSocket nativo. La conexión es explícita y se cierra con disconnect(). */
export function createSocketClient(url: string, options: SocketOptions) {
	let socket: WebSocket | undefined;
	let status: SocketStatus = 'idle';
	const update = (next: SocketStatus) => {
		status = next;
		options.onStatus?.(next);
	};
	return {
		get status() {
			return status;
		},
		connect() {
			if (socket && (socket.readyState === 0 || socket.readyState === 1)) return;
			if (!url.trim()) throw new Error('Falta configurar PUBLIC_WS_URL');
			const parsed = new URL(url);
			if (!['ws:', 'wss:'].includes(parsed.protocol))
				throw new Error('El WebSocket debe usar WS o WSS');
			if (typeof window === 'undefined')
				throw new Error('Conecta el WebSocket desde onMount en el navegador');
			const current = new WebSocket(parsed, options.protocols);
			socket = current;
			update('connecting');
			current.onopen = () => {
				if (socket === current) update('connected');
			};
			current.onmessage = (event) => {
				if (socket !== current) return;
				let message: unknown;
				try {
					if (typeof event.data !== 'string')
						throw new Error('Se esperaba un mensaje JSON de texto');
					message = JSON.parse(event.data);
				} catch {
					options.onError?.(new Error('Mensaje WebSocket inválido: se esperaba JSON de texto'));
					return;
				}
				options.onMessage(message);
			};
			current.onerror = () => {
				if (socket !== current) return;
				update('error');
				options.onError?.(new Error('Error de conexión WebSocket'));
			};
			current.onclose = () => {
				if (socket === current) {
					socket = undefined;
					update('disconnected');
				}
			};
		},
		send(message: unknown) {
			if (!socket || socket.readyState !== 1) throw new Error('El WebSocket no está conectado');
			const text = JSON.stringify(message);
			if (text === undefined) throw new Error('El mensaje debe ser serializable como JSON');
			socket.send(text);
		},
		disconnect() {
			const current = socket;
			socket = undefined;
			if (current) {
				current.onopen = current.onmessage = current.onerror = current.onclose = null;
				current.close(1000, 'Salida de la vista');
			}
			update('disconnected');
		}
	};
}
