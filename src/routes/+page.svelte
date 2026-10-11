<script lang="ts">
  import { onMount } from 'svelte';

  type ViewId = 'overview' | 'monitor' | 'cameras' | 'detections' | 'alerts' | 'service';
  type LogLevel = 'all' | 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG' | 'CRITICAL';
  interface Camera { id: string; number?: number; name: string; url: string; streamPath?: string; registeredAt: string; }
  interface Detection { id?: string; fecha_hora?: string; created_at?: string; clase?: string; class_name?: string; confianza?: number; processing_ms?: number; status?: string; }
  interface Alert extends Detection { status?: string; }
  interface LatestResult { processing_ms?: number; }
  interface InferenceSource { status?: string; frames_received?: number; windows_processed?: number; last_frame_age_seconds?: number | null; latest_result?: LatestResult; }
  interface InferenceResponse { available: boolean; source: InferenceSource | null; latestDetection?: Detection | null; detections?: Detection[]; alerts?: Alert[]; error?: string; }
  interface ServiceLog { id?: string; fecha_hora?: string; camera?: string | null; event_type?: string; level?: string; message?: string; processing_ms?: number | null; inference_ms?: number | null; session_id?: string | null; window_id?: string | null; detection_id?: string | null; }
  interface CameraListResponse { cameras?: Camera[]; }
  interface RegisterCameraResponse { camera: Camera; inference?: { available?: boolean; error?: string }; }
  interface DeleteCameraResponse { warning?: string | null; }
  interface ServiceLogsResponse { logs?: ServiceLog[]; total?: number; }
  interface ErrorPayload { error?: string; }

  const DEFAULT_URL = 'http://15.229.111.228:8889/camera01/';
  const menu: Array<{ id: ViewId; label: string; icon: string }> = [
    { id: 'overview', label: 'Resumen general', icon: '▦' },
    { id: 'monitor', label: 'Monitoreo en vivo', icon: '▣' },
    { id: 'cameras', label: 'Cámaras', icon: '◫' },
    { id: 'detections', label: 'Detecciones', icon: '≋' },
    { id: 'alerts', label: 'Eventos y alertas', icon: '◇' },
    { id: 'service', label: 'Registros del servicio', icon: '☷' }
  ];
  let view = $state<ViewId>('overview');
  let modal = $state(false);
  let cameras = $state<Camera[]>([]);
  let selectedId = $state('');
  let inference = $state<InferenceResponse | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state('');
  let notice = $state('');
  let name = $state('Logitech C922');
  let url = $state(DEFAULT_URL);
  let showPlayer = $state(true);
  let lastRefresh = $state<Date | null>(null);
  let query = $state('');
  let actionFilter = $state('all');
  let since = $state('');
  let until = $state('');
  let serviceLogs = $state<ServiceLog[]>([]);
  let serviceLogsLoading = $state(false);
  let serviceLogsError = $state('');
  let serviceLogsLastRefresh = $state<Date | null>(null);
  let logLevel = $state<LogLevel>('all');
  let logCamera = $state('all');
  let logQuery = $state('');

  let camera = $derived(cameras.find((item) => item.id === selectedId) ?? cameras[0] ?? null);
  let source = $derived(inference?.source ?? null);
  let detections = $derived(inference?.detections ?? (inference?.latestDetection ? [inference.latestDetection] : []));
  let latest = $derived(inference?.latestDetection ?? null);
  let alerts = $derived(inference?.alerts ?? []);
  let aiConnected = $derived(inference?.available === true);
  let aiReceiving = $derived(aiConnected && ['receiving', 'running'].includes(source?.status ?? ''));
  let sourceAvailable = $derived(aiReceiving && (source?.last_frame_age_seconds == null || Number(source.last_frame_age_seconds) <= 10));
  let filteredDetections = $derived(detections.filter((d) => {
    const cls = (d.clase ?? d.class_name ?? '').toLowerCase();
    const date = d.fecha_hora ?? d.created_at ?? '';
    return (actionFilter === 'all' || cls === actionFilter) &&
      (!since || date.slice(0, 10) >= since) && (!until || date.slice(0, 10) <= until) &&
      (!query || cls.includes(query.toLowerCase()));
  }));
  let filteredServiceLogs = $derived(serviceLogs.filter((log) => {
    const level = String(log.level ?? '').toUpperCase();
    const cameraId = String(log.camera ?? '');
    const text = `${log.event_type ?? ''} ${log.message ?? ''} ${log.session_id ?? ''} ${log.window_id ?? ''}`.toLowerCase();
    return (logLevel === 'all' || level === logLevel) &&
      (logCamera === 'all' || cameraId === logCamera) &&
      (!logQuery || text.includes(logQuery.toLowerCase()));
  }));
  let processingTimes = $derived(filteredServiceLogs.map((log) => Number(log.processing_ms)).filter((value) => Number.isFinite(value) && value >= 0));
  let averageProcessingMs = $derived(processingTimes.length ? processingTimes.reduce((sum, value) => sum + value, 0) / processingTimes.length : null);
  let minimumProcessingMs = $derived(processingTimes.length ? Math.min(...processingTimes) : null);
  let maximumProcessingMs = $derived(processingTimes.length ? Math.max(...processingTimes) : null);
  let latestProcessingMs = $derived(source?.latest_result?.processing_ms ?? latest?.processing_ms ?? null);

  function errorMessage(value: unknown): string { return value instanceof Error ? value.message : String(value); }
  async function getJSON<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({})) as T & ErrorPayload;
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  async function loadCamera() {
    try {
      cameras = (await getJSON<CameraListResponse>('/api/camera')).cameras ?? [];
      if (!cameras.some((item) => item.id === selectedId)) selectedId = cameras[0]?.id ?? '';
    }
    catch (e) { error = errorMessage(e); }
    finally { loading = false; }
  }
  async function refreshInference() {
    if (!camera) { inference = null; return; }
    try { inference = await getJSON<InferenceResponse>(`/api/inference?id=${encodeURIComponent(camera.id)}`); }
    catch (e) { inference = { available: false, error: errorMessage(e), source: null, alerts: [], detections: [] }; }
    lastRefresh = new Date();
  }
  async function refreshServiceLogs() {
    serviceLogsLoading = true;
    serviceLogsError = '';
    try {
      const data = await getJSON<ServiceLogsResponse>('/api/service-logs?limit=200');
      serviceLogs = data.logs ?? [];
      serviceLogsLastRefresh = new Date();
    } catch (e) {
      serviceLogs = [];
      serviceLogsError = errorMessage(e);
    } finally {
      serviceLogsLoading = false;
    }
  }
  onMount(() => {
    let mounted = true;
    let fetching = false;
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get('view');
    if (menu.some((item) => item.id === requestedView)) view = requestedView as ViewId;
    if (params.get('modal') === 'camera') modal = true;
    loadCamera().then(() => {
      if (!mounted) return;
      refreshInference();
      if (view === 'service') refreshServiceLogs();
    });
    const timer = setInterval(async () => {
      if (!mounted || !camera || fetching) return;
      fetching = true;
      try { await refreshInference(); } finally { fetching = false; }
    }, 3000);
    return () => { mounted = false; clearInterval(timer); };
  });
  function clearFeedback() { notice = ''; error = ''; }
  async function registerCamera(event: SubmitEvent) {
    event.preventDefault(); clearFeedback(); busy = true;
    try {
      const data = await getJSON<RegisterCameraResponse>('/api/camera', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, url })
      });
      cameras = [...cameras, data.camera];
      selectedId = data.camera.id;
      notice = data.inference?.available ? 'Cámara registrada. Se solicitó el inicio del análisis.' : 'Cámara registrada. La inferencia no respondió; puedes reintentarla.';
      if (data.inference?.error) error = data.inference.error;
      modal = false; view = 'cameras'; showPlayer = true;
      name = ''; url = '';
      await refreshInference();
    } catch (e) { error = errorMessage(e); }
    finally { busy = false; }
  }
  async function activateInference(target: Camera | null = camera) {
    if (!target) return;
    selectedId = target.id;
    clearFeedback(); busy = true;
    try {
      await getJSON<unknown>('/api/camera/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: target.id }) });
      notice = 'Solicitud enviada al servidor de inferencia.';
      await refreshInference();
    } catch (e) { error = errorMessage(e); }
    finally { busy = false; }
  }
  async function deleteCamera(target: Camera) {
    if (!confirm(`¿Detener el análisis y eliminar ${target.id}?`)) return;
    clearFeedback(); busy = true;
    try {
      const result = await getJSON<DeleteCameraResponse>(`/api/camera?id=${encodeURIComponent(target.id)}`, { method: 'DELETE' });
      cameras = cameras.filter((item) => item.id !== target.id);
      selectedId = cameras[0]?.id ?? '';
      inference = null; showPlayer = cameras.length > 0;
      notice = result.warning ?? 'Se eliminó la cámara y se solicitó detener la inferencia.';
      view = 'cameras';
      if (camera) await refreshInference();
    } catch (e) { error = errorMessage(e); }
    finally { busy = false; }
  }
  function fmtDate(value: string | Date | null | undefined) {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('es-PE', { timeZone: 'America/Lima', dateStyle: 'short', timeStyle: 'medium' });
  }
  function confidence(value: unknown) {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—';
  }
  function labelClass(value: unknown) {
    return String(value || 'Sin clasificación').replaceAll('_', ' ');
  }
  function cameraLabel(value: Camera | null | undefined) {
    return value?.number ? `Cámara ${value.number}` : value?.id ?? '—';
  }
  function go(id: ViewId) { view = id; clearFeedback(); if (id === 'service') refreshServiceLogs(); }
  function showRegistration() { clearFeedback(); name = ''; url = ''; modal = true; }
  async function selectCamera(target: Camera, destination: ViewId = 'monitor') {
    selectedId = target.id; inference = null; view = destination; showPlayer = true;
    await refreshInference();
  }
</script>

<svelte:head>
  <title>SentriX — Centro de monitoreo</title>
  <meta name="description" content="Monitoreo, detecciones y alertas de cámaras con MediaMTX y FastAPI" />
</svelte:head>

<div class="shell">
  <aside class="sidebar">
    <a class="brand" href="/" onclick={(e) => { e.preventDefault(); go('overview'); }}>
      <img src="/sentrix-logo.png" alt="SentriX — Sistema de vigilancia inteligente" />
      <span class="brand-lockup"><strong>Sentri<span>X</span></strong><small>Sistema de vigilancia inteligente</small></span>
    </a>
    <div class="menu-label">ESPACIO DE TRABAJO</div>
    <nav aria-label="Navegación principal">
      {#each menu as item}
        <button type="button" class:current={view === item.id} onclick={() => go(item.id)}>
          <span class="nav-icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>
        </button>
      {/each}
    </nav>
    <div class="side-bottom">
      <div class="system-state"><span class:live={aiReceiving} class="led"></span><div><strong>Servicio de inferencia</strong><small>{aiReceiving ? 'Procesando video' : aiConnected ? 'Conectado · sin recepción confirmada' : 'Sin conexión'}</small></div></div>
      <p>Prototipo académico · múltiples cámaras</p>
    </div>
  </aside>

  <div class="workspace">
    <header class="topbar">
      <div class="breadcrumb">OPERACIONES <span>/</span> <strong>{menu.find((m) => m.id === view)?.label}</strong></div>
      <div class="topbar-right"><span class="server-label">São Paulo <i></i> MediaMTX</span><span class="avatar" title="Operador">OP</span><span class="operator">Operador</span></div>
    </header>
    <main>
      <div class="page-head">
        <div>
          <div class="eyebrow">SISTEMA DE VIGILANCIA INTELIGENTE</div>
          <h1>{menu.find((m) => m.id === view)?.label}</h1>
          <p class="subtitle">{view === 'overview' ? 'Una vista clara del estado de tu operación.' : view === 'monitor' ? 'Reproducción en directo y análisis de la cámara registrada.' : view === 'cameras' ? 'Registro y control de las fuentes de video.' : view === 'detections' ? 'Clasificaciones producidas por el servidor de inferencia.' : view === 'alerts' ? 'Eventos confirmados por las reglas de detección.' : 'Estado técnico de las conexiones y el procesamiento.'}</p>
        </div>
        {#if view === 'cameras'}<button class="btn primary" onclick={showRegistration} disabled={loading}>+ Agregar cámara</button>
        {:else if view === 'monitor'}<button class="btn secondary" onclick={() => showPlayer = !showPlayer} disabled={!camera}>{showPlayer ? 'Ocultar reproductor' : 'Mostrar reproductor'}</button>
        {:else}<button class="btn secondary" onclick={refreshInference} disabled={!camera}>↻ Actualizar</button>{/if}
      </div>

      {#if error}<div class="banner error" role="alert"><strong>Atención</strong><span>{error}</span><button aria-label="Cerrar aviso" onclick={() => error = ''}>×</button></div>{/if}
      {#if notice}<div class="banner success" role="status"><strong>Operación completada</strong><span>{notice}</span><button aria-label="Cerrar aviso" onclick={() => notice = ''}>×</button></div>{/if}

      {#if view === 'overview'}
        <div class="stats">
          <div class="stat"><small>Cámaras registradas</small><strong>{String(cameras.length).padStart(2, '0')}</strong><span>Inventario disponible</span></div>
          <div class="stat"><small>Servidor de IA</small><strong class="stat-word">{aiConnected ? 'Conectado' : 'No disponible'}</strong><span>{aiReceiving ? 'Analizando transmisión' : 'Sin procesamiento confirmado'}</span></div>
          <div class="stat"><small>Ventanas procesadas</small><strong>{source?.windows_processed ?? '—'}</strong><span>Reportadas por FastAPI</span></div>
          <div class="stat"><small>Alertas confirmadas</small><strong>{alerts.length}</strong><span>Recibidas para {camera?.id ?? 'la cámara seleccionada'}</span></div>
        </div>
        <div class="columns two-thirds">
          <section class="surface featured">
            <div class="section-title"><div><h2>Cámara principal</h2><p>Fuente activa del piloto</p></div><span class="pill neutral">{camera ? 'Registrada' : 'Sin registrar'}</span></div>
            <div class="camera-feature"><div class="camera-illustration"><span>◉</span></div><div><h3>{camera ? cameraLabel(camera) : 'No hay cámaras registradas'}</h3><p>{camera ? `${camera.name} · Transmisión WebRTC desde AWS` : 'Agrega tu primera cámara para empezar a monitorear.'}</p><button class="btn primary" onclick={() => camera ? go('monitor') : (go('cameras'), showRegistration())}>{camera ? 'Ir al monitoreo →' : 'Registrar cámara →'}</button></div></div>
          </section>
          <section class="surface"><div class="section-title"><div><h2>Última clasificación</h2><p>Resultado más reciente del modelo</p></div></div>
            {#if latest}<div class="classification"><span class="classification-title">{labelClass(latest.clase)}</span><strong>{confidence(latest.confianza)}</strong></div><div class="tiny">{fmtDate(latest.fecha_hora)}</div>
            {:else}<div class="placeholder"><span class="placeholder-icon">◎</span><strong>Sin predicciones recientes</strong><p>Los resultados aparecerán cuando FastAPI procese video.</p></div>{/if}
          </section>
        </div>
        <section class="surface hint-strip"><span>ⓘ</span><p>El video se distribuye desde MediaMTX. La inferencia funciona de forma independiente y puede reconectarse sin interrumpir la visualización.</p></section>

      {:else if view === 'cameras'}
        <section class="surface table-surface">
          <div class="section-title"><div><h2>Inventario de cámaras</h2><p>Fuentes registradas en este entorno</p></div><span class="pill neutral">{cameras.length ? `${cameras.length} ${cameras.length === 1 ? 'registro' : 'registros'}` : 'Sin registros'}</span></div>
          {#if loading}<div class="empty">Cargando cámaras...</div>
          {:else if !cameras.length}<div class="empty"><span class="placeholder-icon">▣</span><h3>Aún no hay cámaras</h3><p>Registra una transmisión para habilitar el monitor.</p><button class="btn primary" onclick={showRegistration}>Agregar cámara</button></div>
          {:else}
            <div class="table-scroll"><table><thead><tr><th>Nombre / identificador</th><th>Origen</th><th>Estado de IA</th><th>Fecha de registro</th><th>Acciones</th></tr></thead>
              <tbody>{#each cameras as item (item.id)}<tr class:selected-row={item.id === camera?.id}><td><strong>{cameraLabel(item)}</strong><small>{item.name}</small></td><td><span class="source-type">MediaMTX · WebRTC</span></td><td><span class:positive={item.id === camera?.id && aiReceiving} class="pill">{item.id === camera?.id ? (aiReceiving ? 'Analizando' : aiConnected ? 'Conectada' : 'Desconectada') : 'Sin consultar'}</span></td><td>{fmtDate(item.registeredAt)}</td><td><div class="table-actions"><button class="text-btn" onclick={() => selectCamera(item)}>Ver monitor</button><button class="text-btn" onclick={() => activateInference(item)} disabled={busy}>Reintentar IA</button><button class="text-btn destructive" onclick={() => deleteCamera(item)} disabled={busy}>Eliminar</button></div></td></tr>{/each}</tbody>
            </table></div>
            <div class="table-foot">Seleccionada: <span>{camera?.name} · {camera?.url}</span></div>
          {/if}
        </section>

      {:else if view === 'monitor'}
        {#if !camera}<div class="surface empty"><span class="placeholder-icon">▣</span><h3>Registra una cámara para empezar</h3><p>El reproductor aparecerá aquí una vez registrada una fuente.</p><button class="btn primary" onclick={() => { go('cameras'); showRegistration(); }}>Registrar cámara</button></div>
        {:else}
          {#if cameras.length > 1}<div class="camera-switcher"><label for="selected-camera">Cámara activa</label><select id="selected-camera" bind:value={selectedId} onchange={refreshInference}>{#each cameras as item}<option value={item.id}>{cameraLabel(item)} · {item.name}</option>{/each}</select></div>{/if}
          <div class="monitor-grid">
            <section class="surface stream-surface">
              <div class="section-title"><div><h2>{cameraLabel(camera)}</h2><p>{camera.name} · MediaMTX · WebRTC</p></div><a class="external" href={camera.url} target="_blank" rel="noopener noreferrer">Abrir original ↗</a></div>
              <div class="stream">
                {#if showPlayer}{#key camera.id}<iframe src={camera.url} title={`Video de ${camera.name}`} allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>{/key}
                {:else}<div class="stream-paused"><span>◉</span><strong>Reproductor oculto</strong><p>La inferencia continúa en el servidor mientras esté activa.</p></div>{/if}
              </div>
              <div class="stream-footer"><span><span class="led live"></span> Fuente registrada</span><span>La reproducción se conecta directamente a AWS</span></div>
            </section>
            <div class="monitor-aside">
              <section class="surface"><div class="section-title"><div><h2>Inteligencia artificial</h2><p>Estado de FastAPI</p></div><span class:positive={aiReceiving} class="pill">{aiReceiving ? 'Analizando' : aiConnected ? 'Conectada' : 'No disponible'}</span></div>
                <div class="metric-list" aria-live="polite"><div class="state-row"><span>Estado de la fuente</span><strong class="status-value" class:status-ok={sourceAvailable} class:status-off={!sourceAvailable}>{sourceAvailable ? 'Activa' : 'No disponible'}</strong></div><div class="state-row"><span>Estado del servicio de inferencia</span><strong class="status-value" class:status-ok={aiConnected} class:status-off={!aiConnected}>{aiConnected ? 'Disponible' : 'No disponible'}</strong></div><div><span>Estado técnico del receptor</span><strong>{source?.status ?? '—'}</strong></div><div><span>Fotogramas recibidos</span><strong>{source?.frames_received ?? '—'}</strong></div><div><span>Ventanas procesadas</span><strong>{source?.windows_processed ?? '—'}</strong></div><div><span>Último procesamiento</span><strong>{latestProcessingMs == null ? '—' : `${Number(latestProcessingMs).toFixed(2)} ms`}</strong></div><div><span>Último fotograma</span><strong>{source?.last_frame_age_seconds == null ? '—' : `${source.last_frame_age_seconds} s`}</strong></div></div>
                <button class="btn secondary full" onclick={() => activateInference()} disabled={busy}>{busy ? 'Solicitando...' : 'Activar / reintentar IA'}</button>
              </section>
              <section class="surface"><div class="section-title"><div><h2>Predicción reciente</h2><p>Resultado de la última ventana</p></div></div>
                {#if latest}<div class="prediction-label">{labelClass(latest.clase)}</div><div class="prediction-confidence">Confianza <strong>{confidence(latest.confianza)}</strong></div><div class="tiny">{fmtDate(latest.fecha_hora)}</div>
                {:else}<div class="placeholder compact"><span class="placeholder-icon">◎</span><p>Esperando predicciones de la IA.</p></div>{/if}
              </section>
            </div>
          </div>
        {/if}

      {:else if view === 'detections'}
        <section class="surface">
          <div class="section-title"><div><h2>Historial de detecciones</h2><p>Resultados disponibles a través de FastAPI</p></div><span class="pill neutral">{filteredDetections.length} resultados</span></div>
          <div class="filters"><label>Buscar acción<input placeholder="Buscar clasificación" bind:value={query}/></label><label>Acción estimada<select bind:value={actionFilter}><option value="all">Todas las acciones</option><option value="normal">Normal</option><option value="hurto">Hurto</option><option value="robo">Robo</option><option value="agresion_fisica">Agresión física</option><option value="vandalismo">Vandalismo</option></select></label><label>Desde<input type="date" bind:value={since}/></label><label>Hasta<input type="date" bind:value={until}/></label></div>
          <div class="table-scroll"><table><thead><tr><th>Fecha y hora</th><th>Cámara</th><th>Acción estimada</th><th>Confianza</th></tr></thead><tbody>
            {#each filteredDetections as detection, i (detection.id ?? `${detection.fecha_hora}-${i}`)}<tr><td>{fmtDate(detection.fecha_hora)}</td><td>{camera?.id ?? '—'}</td><td><strong>{labelClass(detection.clase)}</strong></td><td>{confidence(detection.confianza)}</td></tr>
            {:else}<tr><td colspan="4" class="table-empty">{aiConnected ? 'No hay detecciones para los filtros seleccionados.' : 'Sin registros disponibles. Verifica la conexión con FastAPI.'}</td></tr>{/each}
          </tbody></table></div>
          <p class="footnote">Se muestran las detecciones recientes proporcionadas por la API; el historial completo requiere paginación y almacenamiento del servicio.</p>
        </section>

      {:else if view === 'alerts'}
        <section class="surface"><div class="section-title"><div><h2>Eventos confirmados</h2><p>Las alertas no equivalen a cada clasificación individual</p></div><span class="pill neutral">{alerts.length} eventos</span></div>
          {#if alerts.length}<div class="event-list">{#each alerts as alert, i (alert.id ?? `${alert.fecha_hora}-${i}`)}<div class="event"><span class="event-symbol">!</span><div><strong>{labelClass(alert.clase)}</strong><p>{fmtDate(alert.fecha_hora)} · {camera?.id ?? '—'}</p></div><span class="pill">{alert.status ?? 'Registrado'}</span></div>{/each}</div>
          {:else}<div class="empty"><span class="placeholder-icon">◇</span><h3>No hay alertas confirmadas</h3><p>{aiConnected ? 'Cuando el sistema confirme un evento, aparecerá aquí.' : 'El servidor de inferencia debe estar disponible para consultar eventos.'}</p></div>{/if}
        </section>

      {:else if view === 'service'}
        <div class="stats performance-stats"><div class="stat"><small>Tiempo promedio</small><strong class="stat-word">{averageProcessingMs == null ? '—' : `${averageProcessingMs.toFixed(2)} ms`}</strong><span>{processingTimes.length} ejecuciones con tiempo registrado</span></div><div class="stat"><small>Tiempo mínimo</small><strong class="stat-word">{minimumProcessingMs == null ? '—' : `${minimumProcessingMs.toFixed(2)} ms`}</strong><span>Menor tiempo de los filtros actuales</span></div><div class="stat"><small>Tiempo máximo</small><strong class="stat-word">{maximumProcessingMs == null ? '—' : `${maximumProcessingMs.toFixed(2)} ms`}</strong><span>Mayor tiempo de los filtros actuales</span></div><div class="stat"><small>Errores registrados</small><strong>{filteredServiceLogs.filter((log) => String(log.level).toUpperCase() === 'ERROR').length}</strong><span>{filteredServiceLogs.length} registros visibles</span></div></div>
        <section class="surface table-surface">
          <div class="section-title"><div><h2>Historial técnico de inferencia</h2><p>Ejecuciones, errores, eventos y tiempos guardados por el servicio</p></div><button class="btn secondary" onclick={refreshServiceLogs} disabled={serviceLogsLoading}>{serviceLogsLoading ? 'Consultando...' : '↻ Actualizar'}</button></div>
          <div class="log-filters">
            <label>Buscar<input placeholder="Evento, mensaje, sesión o ventana" bind:value={logQuery}/></label>
            <label>Nivel<select bind:value={logLevel}><option value="all">Todos los niveles</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option><option value="DEBUG">DEBUG</option><option value="CRITICAL">CRITICAL</option></select></label>
            <label>Cámara<select bind:value={logCamera}><option value="all">Todas las cámaras</option>{#each [...new Set(serviceLogs.map((log) => log.camera).filter(Boolean))] as cameraId}<option value={cameraId}>{cameraId}</option>{/each}</select></label>
          </div>
          {#if serviceLogsError}<div class="banner error logs-error"><strong>No se pudieron consultar los logs</strong><span>{serviceLogsError}</span></div>{/if}
          <div class="table-scroll"><table class="logs-table"><thead><tr><th>Fecha y hora</th><th>Nivel</th><th>Cámara</th><th>Tipo de evento</th><th>Procesamiento</th><th>Inferencia</th><th>Mensaje</th></tr></thead><tbody>
            {#each filteredServiceLogs as log, i (log.id ?? `${log.fecha_hora}-${i}`)}<tr><td>{fmtDate(log.fecha_hora)}</td><td><span class:error-level={String(log.level).toUpperCase() === 'ERROR'} class:warning-level={String(log.level).toUpperCase() === 'WARNING'} class="log-level">{log.level ?? '—'}</span></td><td>{log.camera ?? '—'}</td><td><strong>{log.event_type ?? '—'}</strong>{#if log.session_id}<small>Sesión: {log.session_id}</small>{/if}{#if log.window_id}<small>Ventana: {log.window_id}</small>{/if}</td><td>{log.processing_ms == null ? '—' : `${Number(log.processing_ms).toFixed(2)} ms`}</td><td>{log.inference_ms == null ? '—' : `${Number(log.inference_ms).toFixed(2)} ms`}</td><td class="log-message">{log.message ?? '—'}{#if log.detection_id}<small>Detección: {log.detection_id}</small>{/if}</td></tr>
            {:else}<tr><td colspan="7" class="table-empty">{serviceLogsLoading ? 'Consultando registros...' : serviceLogsError ? 'El servicio no está disponible. Usa “Actualizar” cuando FastAPI vuelva a responder.' : 'No hay registros en inference_logs para los filtros seleccionados.'}</td></tr>{/each}
          </tbody></table></div>
          <p class="footnote logs-note">Esta vista presenta únicamente datos de <code>inference_logs</code>. Las detecciones y alertas permanecen almacenadas y consultadas por separado.</p>
        </section>
      {/if}
    </main>
    <footer>SentriX <span>·</span> Centro de monitoreo <span class="footer-right">Prototipo de integración · MediaMTX + FastAPI</span></footer>
  </div>
</div>

{#if modal}
  <div class="modal-overlay" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) modal = false; }}>
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="dialog-top"><div><div class="eyebrow">CONFIGURACIÓN DE FUENTE</div><h2 id="modal-title">Registrar cámara</h2><p>Vincula una transmisión existente de MediaMTX.</p></div><button class="close" aria-label="Cerrar" onclick={() => modal = false}>×</button></div>
      <form onsubmit={registerCamera}>
        <label for="name">Nombre de la cámara <span>*</span></label><input id="name" required maxlength="100" bind:value={name} placeholder="Ej. Cámara de entrada" />
        <label for="url">Dirección WebRTC <span>*</span></label><input id="url" type="url" required bind:value={url} placeholder="http://IP:8889/camera01/" />
        <div class="info-note"><strong>Transmisión existente</strong><p>La cámara debe estar publicando antes de registrarla. Si la ruta ya está registrada, SentriX asignará automáticamente un identificador diferente al nuevo registro.</p></div>
        <div class="modal-actions"><button type="button" class="btn secondary" onclick={() => modal = false}>Cancelar</button><button type="submit" class="btn primary" disabled={busy}>{busy ? 'Registrando...' : 'Registrar y activar IA'}</button></div>
      </form>
    </div>
  </div>
{/if}

<style>
  :global(*){box-sizing:border-box}
  :global(body){margin:0;background:#f5f7fb;color:#172a45;font-family:Inter,'Segoe UI',system-ui,sans-serif;font-size:14px}
  :global(button),:global(input),:global(select){font:inherit}
  :global(button){cursor:pointer}
  .shell{min-height:100vh;display:flex}
  .sidebar{width:247px;flex-shrink:0;background:#0e2545;color:#eaf2fc;display:flex;flex-direction:column;min-height:100vh;position:sticky;top:0;height:100vh}
  .brand{display:flex;align-items:center;gap:11px;padding:28px 25px 26px;color:inherit;text-decoration:none;border-bottom:1px solid #294261}
  .brand strong{font-size:23px;font-weight:800;letter-spacing:-.8px;display:block;line-height:1.1}
  .brand small{display:block;color:#9fb9d3;font-size:10px;letter-spacing:.25px;margin-top:5px}
  .menu-label{font-weight:700;letter-spacing:1.8px;color:#7898bd;font-size:10px;padding:31px 24px 12px}
  nav{display:flex;flex-direction:column;gap:4px;padding:0 12px}
  nav button{display:flex;align-items:center;gap:12px;padding:13px 15px;width:100%;background:transparent;color:#adc3db;text-align:left;border:1px solid transparent;border-radius:8px;font-weight:560;font-size:13px}
  nav button:hover{background:#183758;color:white}
  nav button.current{background:#1d4c80;color:#fff;border-color:#2a6499;box-shadow:inset 3px 0 #45b6e5}
  .nav-icon{font-size:17px;width:22px;text-align:center;color:#8db8dd}
  nav button.current .nav-icon{color:#b7e7ff}
  .side-bottom{margin-top:auto;padding:20px 18px 24px;border-top:1px solid #294261}
  .system-state{display:flex;align-items:flex-start;gap:10px;padding:12px 9px;background:#173453;border-radius:9px}
  .system-state strong,.system-state small{display:block}.system-state strong{font-size:11px}.system-state small{color:#a3bbd1;font-size:10px;margin-top:5px;line-height:1.4}
  .side-bottom p{color:#718fac;font-size:10px;margin:17px 0 0 3px}
  .led{display:inline-block;flex-shrink:0;width:8px;height:8px;background:#8493a5;border-radius:50%;margin-top:3px}.led.live{background:#2eb99b;box-shadow:0 0 0 3px #2eb99b25}
  .workspace{flex:1;min-width:0;display:flex;flex-direction:column}
  .topbar{height:76px;padding:0 44px;background:white;border-bottom:1px solid #e6ebf2;display:flex;align-items:center;justify-content:space-between;gap:14px}
  .breadcrumb{color:#8392a6;letter-spacing:1.1px;font-size:10px;font-weight:750}.breadcrumb span{margin:0 11px;color:#c9d2df}.breadcrumb strong{color:#2f455f;letter-spacing:0;font-size:12px;font-weight:600}
  .topbar-right{display:flex;align-items:center;gap:12px}.server-label{color:#6c7f96;font-size:12px}.server-label i{display:inline-block;background:#42b5a2;width:5px;height:5px;border-radius:50%;margin:0 6px 2px}.avatar{width:33px;height:33px;border-radius:50%;background:#d9e6f4;color:#2b5279;display:grid;place-items:center;font-size:11px;font-weight:800}.operator{font-size:12px;color:#506780}
  main{max-width:1390px;width:100%;margin:0 auto;padding:46px 48px 68px;flex:1}
  .page-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:27px}.eyebrow{color:#6991ae;font-weight:800;letter-spacing:1.5px;font-size:10px}.page-head h1{margin:10px 0 7px;font-size:31px;letter-spacing:-.8px;line-height:1.2;font-weight:750}.subtitle{color:#7c8da2;margin:0;line-height:1.6;font-size:13px}
  .btn{border-radius:7px;padding:10px 15px;border:1px solid transparent;font-weight:650;font-size:12px;white-space:nowrap;transition:background .15s}.btn.primary{background:#1557a1;color:#fff}.btn.primary:hover{background:#0d447f}.btn.secondary{border-color:#d3deea;color:#2c4a6a;background:#fff}.btn.secondary:hover{background:#f1f6fa}.btn:disabled{opacity:.5;cursor:not-allowed}.btn.full{width:100%;margin-top:17px}
  .surface,.stat{background:#fff;border:1px solid #e1e8f0;border-radius:10px;box-shadow:0 3px 14px #122c4a06}.surface{padding:25px}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:22px}.stat{min-height:155px;padding:24px;display:flex;flex-direction:column;justify-content:space-between}.stat small{color:#75869b;font-size:12px;font-weight:600}.stat strong{font-size:34px;letter-spacing:-.5px;color:#133c69;font-variant-numeric:tabular-nums}.stat .stat-word{font-size:20px;letter-spacing:-.3px}.stat span{font-size:11px;color:#91a0b2}.columns{display:grid;gap:22px}.columns.two-thirds{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)}
  .section-title{display:flex;align-items:start;justify-content:space-between;gap:14px;margin-bottom:21px}.section-title h2{font-size:16px;margin:0;color:#1d3654;font-weight:750}.section-title p{font-size:12px;color:#8997a8;margin:6px 0 0}.pill{display:inline-flex;align-items:center;border-radius:5px;font-size:11px;font-weight:700;background:#fff4e8;color:#ac6a22;padding:6px 9px;white-space:nowrap}.pill.positive{background:#e5f6ef;color:#22835e}.pill.neutral{background:#eff3f8;color:#697f96}
  .camera-feature{display:flex;align-items:center;gap:23px;padding:12px 0 6px}.camera-illustration{height:142px;width:174px;flex-shrink:0;background:linear-gradient(145deg,#102c50,#194a76);border-radius:8px;display:grid;place-items:center;color:#a9d9ef;font-size:58px}.camera-feature h3{font-size:18px;margin:0 0 7px}.camera-feature p{color:#7c8da2;font-size:12px;max-width:280px;line-height:1.6;margin:0 0 17px}
  .camera-switcher{display:flex;align-items:center;gap:12px;margin:0 0 14px;padding:12px 15px;background:#fff;border:1px solid #e2e9f2;border-radius:8px}.camera-switcher label{font-weight:700;color:#536a84}.camera-switcher select{min-width:240px;padding:8px 10px;border:1px solid #cbd7e5;border-radius:6px;background:#fff}.selected-row{background:#f4f9ff}
  .placeholder,.empty{text-align:center;padding:43px 22px;color:#788ca3}.placeholder strong{display:block;color:#33506d;font-size:13px}.placeholder p,.empty p{font-size:12px;line-height:1.65;max-width:340px;margin:9px auto 18px}.placeholder-icon{font-size:32px;color:#9caec2;display:block;margin-bottom:10px}.placeholder.compact{padding:22px 5px}.classification{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:30px 0 7px}.classification-title{font-size:21px;font-weight:800;text-transform:capitalize}.classification strong{color:#1d9066;font-size:18px}.tiny{font-size:11px;color:#8799ab}.hint-strip{display:flex;align-items:center;gap:12px;margin-top:22px;background:#f4faff;border-color:#dcebf9;color:#5e7994;padding:15px 20px}.hint-strip span{font-size:19px;color:#307aaf}.hint-strip p{margin:0;line-height:1.5;font-size:12px}
  .table-surface{padding:0}.table-surface .section-title{padding:24px 25px 0}.table-scroll{width:100%;overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}thead{background:#f6f8fb;color:#71849c;font-size:11px}th{padding:15px 19px;font-weight:750;white-space:nowrap}td{padding:19px;border-top:1px solid #edf1f6;color:#5c7289;vertical-align:middle}td strong{display:block;color:#25405d;font-weight:700}td small{display:block;color:#879bb0;margin-top:5px}.source-type{font-size:11px}.table-actions{display:flex;flex-wrap:wrap;gap:5px}.text-btn{border:1px solid #dce6ef;background:white;border-radius:5px;color:#215681;font-size:11px;font-weight:650;padding:7px 9px}.text-btn:hover{background:#eef6ff}.text-btn.destructive{color:#b64d55;border-color:#f2d7da}.text-btn:disabled{opacity:.5}.table-foot{padding:18px 25px;border-top:1px solid #edf1f6;color:#8296aa;font-size:11px}.table-foot span{overflow-wrap:anywhere;color:#4e6b89}.table-empty{text-align:center;color:#95a2b3;padding:45px 15px}.empty{padding:65px 20px}.empty h3{margin:0;color:#2c4966;font-size:16px}
  .monitor-grid{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(270px,.82fr);align-items:start;gap:21px}.monitor-aside{display:flex;flex-direction:column;gap:20px}.stream-surface{padding:20px}.stream{background:#061326;aspect-ratio:16/9;border-radius:7px;overflow:hidden}.stream iframe{width:100%;height:100%;border:0;display:block}.stream-paused{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#b6cce0}.stream-paused span{font-size:50px;color:#4e7ea5}.stream-paused p{font-size:12px;text-align:center;padding:0 20px}.stream-footer{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:16px;color:#7c91a5;font-size:11px}.stream-footer>span{display:flex;align-items:center;gap:7px}.stream-footer .led{margin:0}.external{font-size:11px;color:#21609a;text-decoration:none;white-space:nowrap}.metric-list{display:flex;flex-direction:column}.metric-list>div{border-bottom:1px solid #eef1f5;padding:12px 0;display:flex;justify-content:space-between;gap:10px}.metric-list span{font-size:11px;color:#8596a9}.metric-list strong{font-size:12px;color:#32516c;text-align:right}.prediction-label{font-size:24px;font-weight:800;text-transform:capitalize;margin-top:24px;color:#1c557e}.prediction-confidence{display:flex;justify-content:space-between;gap:10px;padding:16px 0;color:#71879d;font-size:12px}.prediction-confidence strong{color:#1f8b68}
  .filters{display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr;gap:13px;margin-bottom:22px}.filters label{display:flex;flex-direction:column;gap:9px;color:#415d78;font-size:11px;font-weight:700}.filters input,.filters select,.dialog input{width:100%;min-width:0;padding:11px 12px;border:1px solid #d7e1ec;border-radius:6px;background:#fff;color:#23415e;font-size:12px;outline:none}.filters input:focus,.filters select:focus,.dialog input:focus{border-color:#377ab4;box-shadow:0 0 0 3px #9bd5ff3d}.footnote{font-size:11px;color:#8b9aac;line-height:1.6;margin:18px 0 0}.event-list{display:flex;flex-direction:column}.event{display:flex;align-items:center;gap:15px;padding:17px 0;border-bottom:1px solid #ecf1f6}.event-symbol{background:#fff0eb;color:#b85a43;font-weight:800;width:36px;height:36px;display:grid;place-items:center;border-radius:7px}.event div{flex:1}.event strong{color:#2e4862;font-size:13px;text-transform:capitalize}.event p{color:#8194a7;font-size:11px;margin:5px 0}
  .banner{display:flex;align-items:center;gap:12px;padding:13px 16px;margin-bottom:21px;border:1px solid #eed9ce;border-radius:7px;font-size:12px;background:#fff7f2;color:#8c5038;overflow-wrap:anywhere}.banner strong{flex-shrink:0}.banner span{flex:1}.banner button{border:0;background:transparent;font-size:21px;color:inherit;margin-left:auto}.banner.success{border-color:#cbe9d9;background:#f1fbf6;color:#2e7255}
  footer{padding:19px 48px;background:#fff;border-top:1px solid #e6edf4;color:#95a4b4;font-size:10px}footer span{margin:0 7px}.footer-right{float:right}
  .modal-overlay{position:fixed;inset:0;background:#0b1c32a8;display:flex;justify-content:center;align-items:center;padding:18px;z-index:50}.dialog{background:white;width:100%;max-width:530px;border-radius:12px;padding:28px;box-shadow:0 25px 85px #07182f55;max-height:95vh;overflow:auto}.dialog-top{display:flex;justify-content:space-between;gap:15px}.dialog h2{font-size:24px;margin:8px 0}.dialog p{color:#8396aa;font-size:12px;margin:0 0 25px}.close{align-self:start;background:none;border:0;color:#8193a5;font-size:28px}.dialog form{display:flex;flex-direction:column;gap:9px}.dialog label{font-size:12px;color:#294760;font-weight:700;margin-top:10px}.dialog label span{color:#c34e56}.dialog input{font-size:13px;padding:12px}.info-note{background:#eff6fe;border:1px solid #d9e8f7;border-radius:7px;padding:15px;margin-top:14px}.info-note strong{font-size:12px;color:#265c90}.info-note p{margin:6px 0 0;line-height:1.6;color:#527394}.modal-actions{display:flex;justify-content:end;gap:9px;margin-top:19px}
  @media(max-width:1200px){main{padding:35px 26px 55px}.topbar{padding:0 26px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.monitor-grid{grid-template-columns:1fr}.monitor-aside{display:grid;grid-template-columns:1fr 1fr}.filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
  @media(max-width:830px){.sidebar{width:72px}.brand{padding:20px 14px;justify-content:center}.brand>span:nth-child(2),.menu-label,.nav-icon+span,.side-bottom{display:none}nav{padding:20px 8px}nav button{justify-content:center;padding:13px 9px}.nav-icon{font-size:21px}.columns.two-thirds{grid-template-columns:1fr}.topbar{height:64px}.operator,.server-label{display:none}}
  @media(max-width:580px){.sidebar{width:58px}.brand{padding:14px 7px}.page-head{align-items:start;flex-direction:column}.page-head h1{font-size:25px}.stats{grid-template-columns:1fr 1fr}.stat{padding:16px;min-height:125px}.stat strong{font-size:27px}.stat .stat-word{font-size:15px}.monitor-aside{grid-template-columns:1fr}.filters{grid-template-columns:1fr}.camera-feature{flex-direction:column;align-items:start}.camera-illustration{width:100%;height:110px}.stream-footer{flex-direction:column;align-items:start}main{padding:25px 13px}.surface{padding:17px}.topbar{padding:0 13px}.breadcrumb{font-size:8px}footer{padding:14px}.footer-right{display:none}.dialog{padding:20px}}

  /* Identidad visual SentriX: solo presentación, sin cambios de comportamiento. */
  :global(:root){--sx-navy:#06275a;--sx-navy-deep:#041b43;--sx-blue:#075fd3;--sx-blue-hover:#064da9;--sx-cyan:#12d7f2;--sx-canvas:#f3f6fe;--sx-ink:#092650;--sx-muted:#6d829c;--sx-line:#d9e4f1}
  :global(body){background:var(--sx-canvas);color:var(--sx-ink)}
  .sidebar{background:linear-gradient(180deg,var(--sx-navy) 0%,var(--sx-navy-deep) 100%);color:#eaf5ff;padding-top:76px}
  .brand{position:fixed;z-index:20;inset:0 auto auto 0;width:247px;height:76px;padding:6px 18px;background:#fff;border-right:1px solid #e2e9f2;border-bottom:1px solid #e2e9f2;overflow:hidden}
  .brand img{display:block;width:48px;height:55px;object-fit:cover;object-position:top center;flex-shrink:0}
  .brand-lockup{display:block;min-width:0}.brand .brand-lockup strong{display:block;color:var(--sx-navy);font-size:21px;font-weight:850;line-height:1;letter-spacing:-.7px}.brand .brand-lockup strong span{color:var(--sx-blue)}.brand .brand-lockup small{display:block;color:#54718f;font-size:6px;line-height:1.2;letter-spacing:.12px;margin-top:5px;white-space:nowrap}
  .menu-label{color:#8eb5df;padding-top:27px}
  nav button{color:#b9d0e8;font-weight:600;border-radius:7px}
  nav button:hover{background:#0a3977;color:#fff}
  nav button.current{background:#0c4d9a;color:#fff;border-color:#1b65b7;box-shadow:inset 3px 0 var(--sx-cyan)}
  .nav-icon{color:#a9cdeb}nav button.current .nav-icon{color:var(--sx-cyan)}
  .side-bottom{border-top-color:#204574}.system-state{background:#0b376d}
  .topbar{background:#fff;border-bottom-color:#e2e9f2;justify-content:flex-end}.breadcrumb{display:none}
  main{padding-top:38px}.page-head h1{margin-top:0;color:var(--sx-ink);font-size:30px}.page-head .eyebrow{display:none}.subtitle{color:var(--sx-muted)}
  .btn{font-weight:700;transition:background .15s,box-shadow .15s,transform .15s}
  .btn.primary{background:var(--sx-blue);box-shadow:0 4px 10px #075fd31f}.btn.primary:hover{background:var(--sx-blue-hover);transform:translateY(-1px)}.btn:disabled{transform:none}
  .btn.secondary{border-color:#cddbea;color:#174b80}.btn.secondary:hover{background:#eef5ff;border-color:#a9c5e2}
  .surface,.stat{border-color:var(--sx-line);border-radius:9px;box-shadow:0 3px 12px #06275a08}.stat strong,.section-title h2{color:var(--sx-ink)}
  .pill.neutral{background:#edf3f9;color:#54708e}
  .camera-illustration{background:linear-gradient(145deg,var(--sx-navy-deep),var(--sx-blue) 68%,var(--sx-cyan));color:#d5f9ff}
  .camera-switcher{border-color:var(--sx-line)}.selected-row{background:#eef6ff}
  thead{background:#f2f6fb;color:#5c7694}td{border-top-color:#e7eef6;color:#526b84}td strong{color:#173c66}
  .text-btn{border-color:#d3e0ed;color:#0758a8;font-weight:700}.text-btn:hover{background:#eaf4ff;border-color:#a9c9e8}.text-btn.destructive{color:#c54652;border-color:#f0cbd0}
  .filters input,.filters select,.dialog input{border-color:#ccdaea;color:#173b61}.filters input:focus,.filters select:focus,.dialog input:focus{border-color:var(--sx-blue);box-shadow:0 0 0 3px #12d7f224}
  .info-note{background:#eef6ff;border-color:#d0e4f8}.info-note strong{color:#0758a8}
  .log-filters{display:grid;grid-template-columns:2fr 1fr 1fr;gap:13px;padding:0 25px 22px}.log-filters label{display:flex;flex-direction:column;gap:8px;color:#415d78;font-size:11px;font-weight:700}.log-filters input,.log-filters select{width:100%;min-width:0;padding:10px 12px;border:1px solid #ccdaea;border-radius:6px;background:#fff;color:#173b61;font-size:12px;outline:none}.log-filters input:focus,.log-filters select:focus{border-color:var(--sx-blue);box-shadow:0 0 0 3px #12d7f224}.logs-error{margin:0 25px 20px}.logs-table{min-width:1050px}.log-level{display:inline-flex;padding:5px 8px;border-radius:5px;background:#e8f5ef;color:#267454;font-size:10px;font-weight:800}.log-level.warning-level{background:#fff3df;color:#a55e13}.log-level.error-level{background:#fff0f1;color:#bd3f4b}.log-message{min-width:240px;max-width:430px;white-space:normal;line-height:1.5}.log-message small{margin-top:4px}.logs-note{padding:0 25px 20px}.logs-note code{color:#0758a8;background:#eef6ff;padding:2px 5px;border-radius:4px}
  .metric-list .state-row{padding:14px 0}.status-value{display:inline-flex;align-items:center;padding:5px 8px;border-radius:5px;font-size:11px}.status-value::before{content:'';width:7px;height:7px;border-radius:50%;margin-right:6px;background:currentColor}.status-value.status-ok{color:#187653;background:#e5f6ef}.status-value.status-off{color:#a64850;background:#fff0f1}

  @media(max-width:830px){.brand{width:72px;padding:8px;justify-content:center}.brand img{width:52px;height:58px;object-fit:cover;object-position:top center}.brand-lockup{display:none}}
  @media(max-width:580px){.brand{width:58px;padding:7px}.brand img{width:44px;height:50px}.log-filters{grid-template-columns:1fr;padding:0 17px 18px}.logs-error{margin:0 17px 18px}.logs-note{padding:0 17px 18px}}
</style>
