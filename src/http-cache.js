/**
 * http-cache.js
 *
 * Persists HTTP caching headers (ETag, Last-Modified, Content-Length,
 * Content-Type, X-Downloaded-At) next to each downloaded file as
 *
 *   <dir>/.headers.<filename>   (JSON)
 *
 * Exports:
 *   saveHeaders(filePath, response)          – write sidecar after download
 *   loadHeaders(filePath)                    – read sidecar (null if missing)
 *   conditionalHeaders(filePath)             – If-None-Match / If-Modified-Since
 *   checkIntegrity(filePath)                 – compare disk size vs content-length
 *   formatCacheInfo(cache)                   – human-readable one-liner
 *   headerCachePath(filePath)                – sidecar path helper
 */

import fs from 'fs/promises';
import path from 'path';

const TRACKED_HEADERS = [
  'etag',
  'last-modified',
  'content-length',
  'content-type',
  'cache-control',
];

export function headerCachePath(filePath) {
  const dir  = path.dirname(filePath);
  const base = path.basename(filePath);
  return path.join(dir, `.headers.${base}`);
}

export async function saveHeaders(filePath, response) {
  const cache = { 'x-downloaded-at': new Date().toISOString() };
  for (const h of TRACKED_HEADERS) {
    const val = response.headers.get(h);
    if (val) cache[h] = val;
  }
  await fs.writeFile(headerCachePath(filePath), JSON.stringify(cache, null, 2));
}

export async function loadHeaders(filePath) {
  try {
    const raw = await fs.readFile(headerCachePath(filePath), 'utf-8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function conditionalHeaders(filePath) {
  const cache = await loadHeaders(filePath);
  if (!cache) return {};
  const headers = {};
  if (cache['etag']) headers['If-None-Match'] = cache['etag'];
  if (cache['last-modified'] && !cache['etag']) headers['If-Modified-Since'] = cache['last-modified'];
  return headers;
}

/**
 * Compares the actual file size on disk against the cached content-length.
 * Returns:
 *   { ok: true,  diskSize, cachedSize }   – sizes match (or no cache)
 *   { ok: false, diskSize, cachedSize }   – mismatch → file may be truncated
 */
export async function checkIntegrity(filePath) {
  const cache = await loadHeaders(filePath);
  const cachedSize = cache?.['content-length'] ? parseInt(cache['content-length'], 10) : null;
  let diskSize = null;
  try {
    const stat = await fs.stat(filePath);
    diskSize = stat.size;
  } catch {
    return { ok: false, diskSize: null, cachedSize, error: 'file not found' };
  }
  if (cachedSize === null) return { ok: true, diskSize, cachedSize: null, note: 'no content-length cached' };
  return { ok: diskSize === cachedSize, diskSize, cachedSize };
}

/**
 * Sends a HEAD request to url and compares ETag + Content-Length
 * against the cached sidecar.
 * Returns:
 *   { changed: false }                          – ETag matches, file is fresh
 *   { changed: true,  reason, remoteHeaders }   – something changed
 *   { changed: null,  reason }                  – could not determine
 */
export async function checkRemoteChanged(url, filePath, extraHeaders = {}) {
  const cache = await loadHeaders(filePath);
  try {
    const resp = await fetch(url, {
      method: 'HEAD',
      headers: {
        'User-Agent': 'Mozilla/5.0',
        'Accept': '*/*',
        ...(cache?.['etag']          ? { 'If-None-Match':     cache['etag']          } : {}),
        ...(cache?.['last-modified'] ? { 'If-Modified-Since': cache['last-modified'] } : {}),
        ...extraHeaders,
      }
    });

    const remote = {
      etag:          resp.headers.get('etag'),
      lastModified:  resp.headers.get('last-modified'),
      contentLength: resp.headers.get('content-length'),
      status:        resp.status,
    };

    // 304 = definitely not modified
    if (resp.status === 304) return { changed: false, remote };

    // Compare ETags
    if (cache?.['etag'] && remote.etag) {
      if (cache['etag'] === remote.etag) return { changed: false, remote };
      return { changed: true, reason: `ETag changed: ${cache['etag']} → ${remote.etag}`, remote };
    }

    // Fallback: compare content-length
    if (cache?.['content-length'] && remote.contentLength) {
      if (cache['content-length'] === remote.contentLength)
        return { changed: false, remote };
      return { changed: true, reason: `Size changed: ${cache['content-length']} → ${remote.contentLength} bytes`, remote };
    }

    return { changed: null, reason: 'No comparable headers available', remote };
  } catch (e) {
    return { changed: null, reason: `HEAD request failed: ${e.message}` };
  }
}

export function formatCacheInfo(cache, integrityResult = null) {
  if (!cache) return '(no cache)';
  const parts = [];
  if (cache['etag'])           parts.push(`ETag: ${cache['etag']}`);
  if (cache['last-modified'])  parts.push(`Last-Modified: ${cache['last-modified']}`);
  if (cache['content-length']) {
    const size = formatBytes(parseInt(cache['content-length'], 10));
    if (integrityResult && !integrityResult.ok) {
      parts.push(`Size: ${size} \u26A0\uFE0F mismatch (disk: ${formatBytes(integrityResult.diskSize)})`);
    } else {
      parts.push(`Size: ${size} \u2713`);
    }
  }
  if (cache['x-downloaded-at']) parts.push(`Downloaded: ${cache['x-downloaded-at'].split('T')[0]}`);
  return parts.join(' | ') || '(no useful cache info)';
}

export function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
