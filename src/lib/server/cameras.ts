import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { validateCamera, type Camera } from '../camera';
export function createCameraStore(file = resolve('data/cameras.json')) {
	let queue: Promise<unknown> = Promise.resolve();
	async function read(): Promise<Camera[]> {
		try {
			const items: unknown = JSON.parse(await readFile(file, 'utf8'));
			if (!Array.isArray(items)) throw new Error('Registro de cámaras corrupto');
			return items.map((item) => {
				const fields = validateCamera(item);
				if (typeof item.id !== 'string' || typeof item.createdAt !== 'string')
					throw new Error('Registro de cámaras corrupto');
				return { ...fields, id: item.id, createdAt: item.createdAt };
			});
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
			throw error;
		}
	}
	return {
		list: read,
		remove(id: string): Promise<boolean> {
			const operation = queue
				.catch(() => {})
				.then(async () => {
					const items = await read();
					const remaining = items.filter((camera) => camera.id !== id);
					if (remaining.length === items.length) return false;
					const temp = `${file}.${randomUUID()}.tmp`;
					await writeFile(temp, JSON.stringify(remaining, null, 2), 'utf8');
					await rename(temp, file);
					return true;
				});
			queue = operation;
			return operation;
		},
		async get(id: string) {
			return (await read()).find((camera) => camera.id === id);
		},
		add(value: unknown): Promise<Camera> {
			const input = validateCamera(value);
			const operation = queue
				.catch(() => {})
				.then(async () => {
					const cameras = await read();
					if (
						cameras.some(
							(camera) =>
								camera.sourceId === input.sourceId ||
								(camera.room === input.room &&
									camera.participantIdentity === input.participantIdentity)
						)
					)
						throw new Error('Esta fuente o participante ya está registrado.');
					const camera = { ...input, id: randomUUID(), createdAt: new Date().toISOString() };
					cameras.push(camera);
					await mkdir(dirname(file), { recursive: true });
					const temp = `${file}.${randomUUID()}.tmp`;
					await writeFile(temp, JSON.stringify(cameras, null, 2), 'utf8');
					await rename(temp, file);
					return camera;
				});
			queue = operation;
			return operation;
		}
	};
}
export const cameras = createCameraStore(process.env.CAMERA_STORE_PATH);
