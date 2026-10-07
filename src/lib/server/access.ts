import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
const secret = randomBytes(32);
export const cookieName = 'sentrix_access';
export function issueAccess() {
	const payload = `${Date.now() + 2 * 60 * 60 * 1000}.${randomBytes(16).toString('hex')}`;
	return `${payload}.${createHmac('sha256', secret).update(payload).digest('hex')}`;
}
export function validAccess(value: string | undefined) {
	if (!value || !/^\d+\.[a-f0-9]{32}\.[a-f0-9]{64}$/.test(value)) return false;
	const [expires, nonce, signature] = value.split('.');
	const expected = createHmac('sha256', secret).update(`${expires}.${nonce}`).digest();
	return Number(expires) > Date.now() && timingSafeEqual(expected, Buffer.from(signature, 'hex'));
}
