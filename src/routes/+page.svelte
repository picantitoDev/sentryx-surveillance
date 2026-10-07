<svelte:head>
    <title>Resumen general | SentriX</title>
</svelte:head>

<div class="mx-auto max-w-7xl p-4 sm:p-8">
    <h1 class="text-3xl font-bold text-slate-800">Resumen general</h1>
    <p class="mt-2 text-sm text-slate-600">
        Administra tus cámaras y consulta el monitoreo de SentriX.
    </p>

    <div class="mt-6 grid gap-6 sm:grid-cols-2">
        <section class="rounded-xl border border-slate-200 bg-white p-6">
            <h2 class="text-lg font-semibold text-slate-800">Cámaras</h2>
            <p class="mt-2 text-sm text-slate-600">
                Registra las fuentes de video que deseas visualizar.
            </p>
            <a href="/cameras"
               class="mt-5 inline-flex rounded-lg bg-blue-700 px-4 py-3 text-sm font-semibold text-white">
                Administrar cámaras
            </a>
        </section>

        <section class="rounded-xl border border-slate-200 bg-white p-6">
            <h2 class="text-lg font-semibold text-slate-800">Monitoreo en vivo</h2>
            <p class="mt-2 text-sm text-slate-600">
                Visualiza una cámara registrada y los resultados del servidor de inferencia.
            </p>
            <a href="/monitoring"
               class="mt-5 inline-flex rounded-lg bg-blue-700 px-4 py-3 text-sm font-semibold text-white">
                Abrir monitoreo
            </a>
        </section>
    </div>
</div>
