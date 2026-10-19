/**
 * Favicon and official brand icon candidate resolver (iTab pattern).
 *
 * Provides an intelligent fallback chain:
 * 1. Local bundled official icon (fast, reliable, works offline)
 * 2. Origin official web app icon / favicons (apple-touch-icon, favIcon.svg, favicon.ico)
 * 3. Google high-resolution 128px favicon service
 */

export function getFaviconCandidates(url: string, localIcon?: string): string[] {
  const candidates: string[] = []

  if (localIcon) {
    candidates.push(localIcon)
  }

  if (!url) {
    return candidates
  }

  try {
    const fullUrl = url.startsWith('http') ? url : `https://${url}`
    const parsed = new URL(fullUrl)
    const origin = parsed.origin

    candidates.push(`${origin}/apple-touch-icon.png`)
    candidates.push(`${origin}/favIcon.svg`)
    candidates.push(`${origin}/favicon.ico`)
    candidates.push(
      `https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=${encodeURIComponent(
        origin
      )}&size=128`
    )

    return candidates
  } catch {
    return candidates
  }
}
