export class ApiError extends Error {
	status: number;
	body: unknown;
	constructor(status: number, body: unknown) {
		super(`La API respondió con HTTP ${status}`);
		this.name = 'ApiError';
		this.status = status;
		this.body = body;
	}
}

export type RequestOptions = Omit<RequestInit, 'body'> & { json?: unknown; timeoutMs?: number };

/** Cliente JSON. No hace peticiones hasta llamar a request(). */
export function createApiClient(baseUrl: string, fetcher: typeof fetch = fetch) {
	return {
		async request(path: string, options: RequestOptions = {}): Promise<unknown> {
			if (!baseUrl.trim()) throw new Error('Falta configurar PUBLIC_API_BASE_URL');
			const base = new URL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`);
			if (!['http:', 'https:'].includes(base.protocol))
				throw new Error('La API debe usar HTTP o HTTPS');
			if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(path))
				throw new Error('Usa una ruta relativa a la API');
			const url = new URL(path.replace(/^\/+/, ''), base);
			if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname))
				throw new Error('Ruta fuera de la API configurada');
			const { json, timeoutMs = 15000, signal, ...init } = options;
			if (!Number.isFinite(timeoutMs) || timeoutMs <= 0)
				throw new Error('timeoutMs debe ser positivo');
			const controller = new AbortController();
			const abort = () => controller.abort(signal?.reason);
			if (signal?.aborted) abort();
			else signal?.addEventListener('abort', abort, { once: true });
			const timer = setTimeout(
				() => controller.abort(new Error('Tiempo de espera agotado')),
				timeoutMs
			);
			try {
				const headers = new Headers(init.headers);
				if (!headers.has('Accept')) headers.set('Accept', 'application/json');
				if (json !== undefined && !headers.has('Content-Type'))
					headers.set('Content-Type', 'application/json');
				const response = await fetcher(url, {
					...init,
					headers,
					signal: controller.signal,
					body: json === undefined ? undefined : JSON.stringify(json)
				});
				const text = await response.text();
				let body: unknown = text || undefined;
				if (text && response.headers.get('content-type')?.includes('json')) {
					try {
						body = JSON.parse(text);
					} catch {
						if (response.ok) throw new Error('La API devolvió JSON inválido');
					}
				}
				if (!response.ok) throw new ApiError(response.status, body);
				return body;
			} finally {
				clearTimeout(timer);
				signal?.removeEventListener('abort', abort);
			}
		}
	};
}
