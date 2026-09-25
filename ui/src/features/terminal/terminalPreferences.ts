/** Match Classic's saved scrollback normalization and daemon defaults. */
export function terminalScrollback(value: unknown): number {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed >= 100 && parsed <= 100_000 ? parsed : 2_000;
}

export function terminalAppearance(targetWindow: Window) {
  const style = targetWindow.getComputedStyle(targetWindow.document.documentElement);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const size = Number.parseFloat(token('--terminal-font-size', '12'));
  const accent = token('--accent', '#8da2fb');
  return {
    fontSize: Number.isFinite(size) && size > 0 ? size : 12,
    theme: {
      background: token('--background', '#0d0f13'),
      foreground: token('--text', '#e5e8ee'),
      cursor: accent,
      selectionBackground: /^#[0-9a-f]{6}$/i.test(accent) ? `${accent}4d` : 'rgba(141,162,251,.3)',
    },
  };
}
