import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
if (existsSync('.env')) process.loadEnvFile('.env');
process.env.HOST ||= '127.0.0.1';
process.env.PORT ||= '5173';
process.env.ORIGIN ||= `http://localhost:${process.env.PORT}`;
process.env.CAMERA_STORE_PATH ||= resolve('data/cameras.json');
await import('../build/index.js');
