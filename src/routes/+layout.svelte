<script lang="ts">
	import './layout.css';
	const favicon = '/sentrix-logo.png';
	import { page } from '$app/state';
	let { children } = $props();
	let menuOpen = $state(false);
	const navigation = [
		{ href: '/', label: 'Resumen general', icon: '▦' },
		{ href: '/monitoring', label: 'Monitoreo en vivo', icon: '▣' },
		{ href: '/cameras', label: 'Cámaras', icon: '⊞' },
		{ href: '/detections', label: 'Detecciones', icon: '≡' },
		{ href: '/events', label: 'Eventos y alertas', icon: '◇' }
	];
	function active(href: string) {
		return href === '/'
			? page.url.pathname === '/'
			: page.url.pathname === href || page.url.pathname.startsWith(href + '/');
	}
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
<header class="brand-header">
	<a href="/" class="brand" aria-label="SentriX, resumen general" onclick={() => (menuOpen = false)}
		><span class="brand-mark"><img src="/sentrix-logo.png" alt="" /></span><span class="brand-copy"
			><span class="brand-name">Sentri<span class="brand-x">X</span></span><span
				class="brand-tagline">Sistema de vigilancia inteligente</span
			></span
		></a
	>
	<button
		type="button"
		class="menu-toggle"
		aria-label={menuOpen ? 'Cerrar menú' : 'Abrir menú'}
		aria-expanded={menuOpen}
		aria-controls="workspace-menu"
		onclick={() => (menuOpen = !menuOpen)}>{menuOpen ? 'Cerrar ✕' : 'Menú ☰'}</button
	>
</header>
<div class="app-shell">
	<aside id="workspace-menu" class="sidebar" class:menu-open={menuOpen}>
		<p class="workspace-label">ESPACIO DE TRABAJO</p>
		<nav aria-label="Menú principal">
			{#each navigation as item (item.href)}
				<a
					href={item.href}
					class:active={active(item.href)}
					aria-current={active(item.href) ? 'page' : undefined}
					onclick={() => (menuOpen = false)}
					><span aria-hidden="true" class="nav-icon">{item.icon}</span><span>{item.label}</span></a
				>
			{/each}
		</nav>
	</aside>
	<main class="workspace-content">{@render children()}</main>
</div>

<style>
	:global(body) {
		margin: 0;
		background: #f3f7fd;
		font-family: Arial, Helvetica, sans-serif;
	}
	.brand-header {
		height: 86px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0 32px;
		background: #fff;
		border-bottom: 1px solid #e1e7ef;
	}
	.brand {
		display: inline-flex;
		align-items: center;
		gap: 13px;
		color: #061c46;
		font-size: 21px;
		font-weight: 900;
		letter-spacing: 2px;
		text-decoration: none;
	}
	.brand-mark {
		position: relative;
		display: block;
		flex: 0 0 52px;
		width: 52px;
		height: 58px;
		overflow: hidden;
	}
	.brand-mark img {
		position: absolute;
		max-width: none;
		width: 104px;
		height: 104px;
		left: -26px;
		top: -10px;
	}
	.brand-copy {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.brand-name {
		font-size: 28px;
		line-height: 1;
		letter-spacing: -1px;
	}
	.brand-x {
		color: #0076ff;
	}
	.brand-tagline {
		font-size: 10px;
		font-weight: 500;
		letter-spacing: 0.3px;
		color: #526984;
	}
	.app-shell {
		display: flex;
		min-height: calc(100dvh - 86px);
	}
	.sidebar {
		width: 240px;
		flex: 0 0 240px;
		background: #061c46;
		padding: 35px 17px;
	}
	.workspace-label {
		margin: 0 13px 26px;
		color: #8eaed6;
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 1.6px;
	}
	nav {
		display: grid;
		gap: 8px;
	}
	nav a {
		display: flex;
		align-items: center;
		gap: 16px;
		min-height: 55px;
		padding: 12px 16px;
		border: 1px solid transparent;
		border-radius: 7px;
		color: #b9cfee;
		font-size: 14px;
		font-weight: 600;
		text-decoration: none;
	}
	nav a:hover {
		background: #10366b;
		color: #e2faff;
	}
	nav a.active {
		background: #123d79;
		border-color: #24599b;
		box-shadow: inset 3px 0 #00cbea;
		color: #66e8ff;
	}
	.nav-icon {
		width: 14px;
		text-align: center;
		font-size: 17px;
	}
	.workspace-content {
		flex: 1;
		min-width: 0;
	}
	.menu-toggle {
		display: none;
		border: 1px solid #d4dee7;
		border-radius: 7px;
		padding: 8px 12px;
		color: #061c46;
		background: white;
	}
	a:focus-visible,
	button:focus-visible {
		outline: 3px solid #0076ff;
		outline-offset: 3px;
	}
	@media (max-width: 767px) {
		.brand-header {
			height: 76px;
			padding: 0 20px;
		}
		.brand {
			font-size: 17px;
		}
		.menu-toggle {
			display: block;
		}
		.app-shell {
			display: block;
			min-height: calc(100dvh - 76px);
		}
		.sidebar {
			display: none;
			width: 100%;
			padding: 22px 17px;
		}
		.sidebar.menu-open {
			display: block;
		}
		.workspace-label {
			margin-bottom: 16px;
		}
	}
</style>
