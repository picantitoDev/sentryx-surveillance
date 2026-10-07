export type CameraInput = {
	name: string;
	location: string;
	sourceId: string;
	room: string;
	participantIdentity: string;
};
export type Camera = CameraInput & { id: string; createdAt: string };
export function validateCamera(value: unknown): CameraInput {
	if (!value || typeof value !== 'object' || Array.isArray(value))
		throw new Error('Registro de cámara inválido');
	const data = value as Record<string, unknown>;
	const result = {} as CameraInput;
	for (const key of ['name', 'location', 'sourceId', 'room', 'participantIdentity'] as const) {
		const item = data[key];
		if (
			typeof item !== 'string' ||
			!item.trim() ||
			item.trim().length > 100 ||
			/[\u0000-\u001f]/.test(item)
		)
			throw new Error(`Completa correctamente el campo ${key} (máximo 100 caracteres).`);
		result[key] = item.trim();
	}
	return result;
}
