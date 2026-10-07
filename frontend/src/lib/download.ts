/**
 * Handing a blob to the browser as a file save.
 *
 * Small on purpose, and separate from the API client because it is the one part
 * of the download path that touches the DOM rather than the network.
 *
 * Why not an `<a href download>` pointing at the API: a navigation carries no
 * `Authorization` header, so the request is unauthenticated and the button does
 * nothing at all. The bytes are fetched with the token first (`requestBlob`) and
 * only then offered to the browser, which is why this takes a blob and not a URL.
 */

/** Ask the browser to save `blob` under `filename`. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  // Not `display: none` — Firefox ignores the click on a detached element, and
  // a zero-size off-screen anchor is the form that works everywhere.
  anchor.style.position = 'fixed'
  anchor.style.left = '-10000px'
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)

  // Revoked on a later turn of the loop, not immediately: revoking in the same
  // tick can cancel the download in some browsers. A minute is far longer than
  // the browser needs to take a reference and far shorter than a session.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
