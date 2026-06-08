/**
 * http-cache.js
 *
 * Persists HTTP caching headers (ETag, Last-Modified, Content-Length,
 * Content-Type, X-Downloaded-At) next to each downloaded file as
 *
 *   <dir>/.headers.<filename>   (JSON, no extension on the cache key)
 *
 * Example:
 *   downloads/specs/TS/en_319401v030201p.pdf
 *   downloads/specs/TS/.headers.en_319401v030201p.pdf
 *
 * Usage:
 *   import { saveHeaders, loadHeaders, conditionalHeaders } from '../src/http-cache.js';
 *
 *   // After a successful download:
 *   await saveHeaders(filePath, response);
 *
 *   // Before a re-download request:
 *   const condHeaders = await conditionalHeaders(filePath);
 *   // condHeaders may contain If-None-Match and/or If-Modified-Since
 *
 *   // To check a 304 response:
 *   if (response.status === 304) { ... } // file is still fresh
 */

import fs from 'fs/promises';
import path from 'path';

/** Headers we persist. */
const TRACKED_HEADERS = [
  'etag',
  'last-modified',
  'content-length',
  'content-type',
  'cache-control',
];

/**
 * Returns the path of the sidecar cache file for a given downloaded file.
 *   /foo/bar/TS/en_319401v030201p.pdf
 *   → /foo/bar/TS/.headers.en_319401v030201p.pdf
 */
export function headerCachePath(filePath) {
  const dir  = path.dirname(filePath);
  const base = path.basename(filePath);
  return path.join(dir, `.headers.${base}`);
}

/**
 * Saves relevant HTTP response headers as a JSON sidecar file.
 * Adds an `x-downloaded-at` timestamp.
 *
 * @param {string}   filePath  Absolute path of the downloaded file.
 * @param {Response} response  The fetch Response object.
 */
export async function saveHeaders(filePath, response) {
  const cache = { 'x-downloaded-at': new Date().toISOString() };
  for (const h of TRACKED_HEADERS) {
    const val = response.headers.get(h);
    if (val) cache[h] = val;
  }
  await fs.writeFile(headerCachePath(filePath), JSON.stringify(cache, null, 2));
}

/**
 * Loads the sidecar cache JSON for a file.
 * Returns null if no cache exists.
 *
 * @param {string} filePath  Absolute path of the downloaded file.
 * @returns {object|null}
 */
export async function loadHeaders(filePath) {
  try {
    const raw = await fs.readFile(headerCachePath(filePath), 'utf-8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Builds conditional request headers for a file based on its cached
 * HTTP headers.  Returns an object (possibly empty) to spread into
 * a fetch() headers option.
 *
 * Adds:
 *   If-None-Match       when ETag is cached
 *   If-Modified-Since   when Last-Modified is cached (and no ETag)
 *
 * @param {string} filePath  Absolute path of the previously downloaded file.
 * @returns {Promise<object>}  Headers to add to the next request.
 */
export async function conditionalHeaders(filePath) {
  const cache = await loadHeaders(filePath);
  if (!cache) return {};

  const headers = {};
  if (cache['etag']) {
    headers['If-None-Match'] = cache['etag'];
  }
  // Use If-Modified-Since as secondary signal (RFC 7232 s.6: prefer ETag)
  if (cache['last-modified'] && !cache['etag']) {
    headers['If-Modified-Since'] = cache['last-modified'];
  }
  return headers;
}

/**
 * Formats cached header info as a human-readable one-liner for console output.
 * Example: "ETag: \"abc123\" | Last-Modified: Mon, 01 Jan 2024 | Downloaded: 2024-03-15T10:00:00Z"
 *
 * @param {object} cache  Result of loadHeaders().
 * @returns {string}
 */
export function formatCacheInfo(cache) {
  if (!cache) return '(no cache)';
  const parts = [];
  if (cache['etag'])          parts.push(`ETag: ${cache['etag']}`);
  if (cache['last-modified']) parts.push(`Last-Modified: ${cache['last-modified']}`);
  if (cache['content-length']) parts.push(`Size: ${formatBytes(parseInt(cache['content-length'], 10))}`);
  if (cache['x-downloaded-at']) parts.push(`Downloaded: ${cache['x-downloaded-at'].split('T')[0]}`);
  return parts.join(' | ') || '(no useful cache info)';
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
