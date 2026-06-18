/**
 * Deep device fingerprint — collects hardware signals that are hard to spoof.
 *
 * Returns a SHA-256 hash of the combined signals plus the raw fingerprint data
 * so the backend can store both and re-verify on future logins.
 */
export async function collectDeepFingerprint(): Promise<{
  hash: string;
  signals: Record<string, any>;
}> {
  const signals: Record<string, any> = {};

  // ── Hard to spoof ───────────────────────────────────────────────────

  // 1. Canvas fingerprint — GPU/driver dependent rendering
  signals.canvas = await getCanvasFingerprint();

  // 2. WebGL — GPU model, renderer, vendor
  signals.webgl = getWebGLFingerprint();

  // 3. AudioContext — audio stack dependent
  signals.audio = await getAudioFingerprint();

  // 4. Installed fonts — OS + language pack dependent
  signals.fonts = await getInstalledFonts();

  // 5. Screen color depth + gamut — display hardware
  signals.color_depth = screen.colorDepth;
  signals.pixel_ratio = devicePixelRatio;
  signals.color_gamut = (screen as any).colorGamut || 'unknown';

  // 6. Touch support
  signals.max_touch_points = navigator.maxTouchPoints;
  signals.hover_support = window.matchMedia('(hover: hover)').matches;

  // 7. Hardware concurrency + memory
  signals.cpu_cores = navigator.hardwareConcurrency || 0;
  signals.device_memory = (navigator as any).deviceMemory || 0;

  // 8. Media devices (camera/mic models — requires permission)
  signals.media_devices = await getMediaDeviceLabels();

  // ── Moderate to spoof ───────────────────────────────────────────────

  signals.platform = (navigator as any).platform || '';
  signals.user_agent = navigator.userAgent;
  signals.languages = navigator.languages || [];
  signals.timezone = new Date().getTimezoneOffset();
  signals.screen_width = screen.width;
  signals.screen_height = screen.height;
  signals.screen_avail_width = screen.availWidth;
  signals.screen_avail_height = screen.availHeight;

  // Compute stable hash from the combined signals
  const raw = JSON.stringify(signals, Object.keys(signals).sort());
  const hashBuffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
  const hash = Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');

  return { hash, signals };
}

// ── Canvas fingerprint ────────────────────────────────────────────────
async function getCanvasFingerprint(): Promise<string> {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d')!;

  // Draw text + shapes with specific colors — GPU renders these differently
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = '#f60';
  ctx.fillRect(100, 1, 62, 20);
  ctx.fillStyle = '#069';
  ctx.font = '11pt Arial';
  ctx.fillText('Cwm fjordbank glyphs vext quiz, 😃', 2, 15);
  ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
  ctx.font = '18pt Times New Roman';
  ctx.fillText('abcdefghijklmnopqrstuvwxyz', 4, 45);
  ctx.fillStyle = '#333';
  ctx.font = 'bold 14pt Impact';
  ctx.fillText('ABC123', 10, 70);

  // Draw a complex SVG-style path
  ctx.beginPath();
  ctx.arc(50, 100, 30, 0, Math.PI * 2, true);
  ctx.arc(80, 100, 20, 0, Math.PI * 2, false);
  ctx.fillStyle = '#0f0';
  ctx.fill();

  const dataUrl = canvas.toDataURL();
  const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(dataUrl));
  return Array.from(new Uint8Array(buffer)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ── WebGL fingerprint ─────────────────────────────────────────────────
function getWebGLFingerprint(): Record<string, string | boolean | null> {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return { supported: false };
    const webgl = gl as WebGLRenderingContext;

    const ext = webgl.getExtension('WEBGL_debug_renderer_info');
    return {
      supported: true,
      vendor: webgl.getParameter(ext!.UNMASKED_VENDOR_WEBGL),
      renderer: webgl.getParameter(ext!.UNMASKED_RENDERER_WEBGL),
      version: webgl.getParameter(webgl.VERSION),
      shading_language_version: webgl.getParameter(webgl.SHADING_LANGUAGE_VERSION),
    };
  } catch {
    return { supported: false, error: 'blocked' };
  }
}

// ── Audio fingerprint ─────────────────────────────────────────────────
async function getAudioFingerprint(): Promise<string> {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sawtooth';
    osc.frequency.value = 440;
    gain.gain.value = 0.1;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(0);
    osc.stop(0.05);

    // Wait for audio processing
    await new Promise((r) => setTimeout(r, 100));
    await ctx.close();

    // Derive a hash from the audio stack — different audio stacks produce
    // slightly different signal processing results
    const audioSig = `${ctx.sampleRate}-${ctx.baseLatency}-${ctx.outputLatency}`;
    const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(audioSig));
    return Array.from(new Uint8Array(buffer)).map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return 'audio-unavailable';
  }
}

// ── Installed fonts ──────────────────────────────────────────────────
const FONT_TEST_STRING = 'mmiiwWM';
const BASE_FONTS = [
  'monospace', 'sans-serif', 'serif',
  'Arial', 'Helvetica', 'Times New Roman', 'Courier New',
  'Georgia', 'Verdana', 'Trebuchet MS',
];

async function getInstalledFonts(): Promise<string[]> {
  const detector = document.createElement('canvas');
  const ctx = detector.getContext('2d')!;
  const installed: string[] = [];

  for (const font of BASE_FONTS) {
    ctx.font = `16px ${font}`;
    const baseline = ctx.measureText(FONT_TEST_STRING).width;

    // Test with a font that's unlikely to be installed exactly
    ctx.font = `16px "${font}", monospace`;
    const testWidth = ctx.measureText(FONT_TEST_STRING).width;

    // If they differ, the font is installed
    if (Math.abs(testWidth - baseline) > 0.5) {
      installed.push(font);
    }
  }

  return installed;
}

// ── Media devices ─────────────────────────────────────────────────────
async function getMediaDeviceLabels(): Promise<string[]> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((d) => d.label)
      .map((d) => `${d.kind}:${d.label}`);
  } catch {
    return [];
  }
}
