import { inflateSync } from "node:zlib";

/**
 * Minimal PNG decoder for the 8-bit, non-interlaced RGBA/RGB images Playwright
 * produces. Used to check that the sky-viewer canvas actually shows tiles
 * rather than a flat background.
 */
export interface DecodedPng {
  width: number;
  height: number;
  channels: number;
  /** Row-major samples, `channels` bytes per pixel. */
  pixels: Uint8Array;
}

export function decodePng(buf: Buffer): DecodedPng {
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error("not a PNG");
  let pos = 8;
  let width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0;
  const idat: Buffer[] = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString("ascii", pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    pos += 12 + len;
  }
  if (bitDepth !== 8 || interlace !== 0) throw new Error(`unsupported PNG (depth ${bitDepth}, interlace ${interlace})`);
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 4 ? 2 : 1;
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = new Uint8Array(width * height * channels);
  let prev = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const out = pixels.subarray(y * stride, (y + 1) * stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= channels ? out[i - channels] : 0;
      const b = prev[i];
      const c = i >= channels ? prev[i - channels] : 0;
      let v: number;
      switch (filter) {
        case 0: v = line[i]; break;
        case 1: v = line[i] + a; break;
        case 2: v = line[i] + b; break;
        case 3: v = line[i] + ((a + b) >> 1); break;
        case 4: {
          const p = a + b - c;
          const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          v = line[i] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
          break;
        }
        default: throw new Error(`bad PNG filter ${filter}`);
      }
      out[i] = v & 0xff;
    }
    prev = out;
  }
  return { width, height, channels, pixels };
}

export interface PixelStats {
  /** Number of distinct RGB triples among the sampled pixels. */
  distinctColors: number;
  /** Standard deviation of pixel luminance (0..255). */
  luminanceStd: number;
  sampled: number;
}

/** Summarise how "non-uniform" an image is, sampling every `step`-th pixel. */
export function pixelStats(img: DecodedPng, step = 3): PixelStats {
  const colors = new Set<number>();
  let n = 0, sum = 0, sumSq = 0;
  for (let y = 0; y < img.height; y += step) {
    for (let x = 0; x < img.width; x += step) {
      const i = (y * img.width + x) * img.channels;
      const r = img.pixels[i], g = img.channels >= 3 ? img.pixels[i + 1] : r, b = img.channels >= 3 ? img.pixels[i + 2] : r;
      colors.add((r << 16) | (g << 8) | b);
      const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      n++; sum += lum; sumSq += lum * lum;
    }
  }
  const mean = sum / n;
  return { distinctColors: colors.size, luminanceStd: Math.sqrt(Math.max(0, sumSq / n - mean * mean)), sampled: n };
}
