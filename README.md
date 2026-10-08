# sv

Everything you need to build a Svelte project, powered by [`sv`](https://github.com/sveltejs/cli).

## Creating a project

If you're seeing this, you've probably already done this step. Congrats!

```sh
# create a new project
npx sv create my-app
```

To recreate this project with the same configuration:

```sh
# recreate this project
npx sv@0.17.1 create --template minimal --types ts --add prettier eslint tailwindcss="plugins:none" vitest="usages:component,unit" --install npm .
```

## Developing

Once you've created a project and installed dependencies with `npm install` (or `pnpm install` or `yarn`), start a development server:

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

## Building

To create a production version of your app:

```sh
npm run build
```

You can preview the production build with `npm run preview`.

> To deploy your app, you may need to install an [adapter](https://svelte.dev/docs/kit/adapters) for your target environment.


## Backend de inferencia SentriX

El frontend SvelteKit permanece en la raíz (`src/`, `static/`, `package.json`).
El backend Python está en `backend/`, y los adaptadores/metadatos de VideoMAE
en `modelo/`. Los pesos se distribuyen aparte.

- [Instalación, ejecución y pruebas del backend](backend/README.md)
- [Modelo y obtención de pesos](modelo/README.md)
- [Integración con el frontend y LiveKit](docs/backend/INTEGRACION_REPOSITORIO.md)
- [Contrato de resultados](docs/backend/RESULTADOS_VIDEOMAE.md)
- [Eventos confirmados](docs/backend/CONTRATO_ALERTAS_FRONTEND.md)
- [Logs operativos](docs/backend/CONTRATO_LOGS_FRONTEND.md)

La configuración privada del backend vive en `backend/.env`; el `.env` de la
raíz pertenece al frontend. Ninguno se versiona. Ejecutar un único proceso del
backend. Las rutas WebRTC directas corresponden al modo alternativo explícito.

- [PostgreSQL, migración y usuarios con Argon2id](docs/backend/DATABASE.md)
